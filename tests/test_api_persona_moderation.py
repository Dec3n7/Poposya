"""Модерация кастомных персон серверов (0044).

Проверяем: premium-гейт на создание заявки; черновик НЕ назначен серверу до
одобрения (бот его не видит); полный цикл create→submit→approve назначает
персону; reject возвращает причину и не назначает; посторонний не правит чужой
черновик; одобренную (живую) персону менеджер править уже не может.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.container import assemble_container
from src.api.security import SESSION_COOKIE, Session, SessionGuild, encode_session
from src.application.interfaces.entitlements import PlanTier
from src.config import Settings

GUILD = 10
OTHER = 99
OPERATOR = 1
MANAGER = 2  # админ GUILD, НЕ оператор
OUTSIDER = 3  # админ другого сервера


def make_settings(**over):
    base = {
        "discord_token": "t",
        "discord_client_id": "cid",
        "discord_client_secret": "csec",
        "web_session_secret": "test-session-secret-at-least-32-bytes!!",
        "web_operator_ids": [OPERATOR],
    }
    base.update(over)
    return Settings(_env_file=None, **base)


@pytest.fixture
async def container(session_factory):
    c = assemble_container(make_settings(), session_factory.kw["bind"], session_factory)
    c.bot_guilds.prime({GUILD})
    await c.persona.load_all()  # поднять дефолт-персону (как lifespan в проде)
    return c


def _cookie(settings, user_id, guild_ids=(GUILD,)):
    session = Session(
        user_id=user_id,
        username="u",
        avatar=None,
        guilds=[SessionGuild(id=g, name="G", icon=None) for g in guild_ids],
    )
    return encode_session(settings.web_session_secret, session, 24, settings.web_session_version)


def _client(container, user_id, guild_ids=(GUILD,)):
    app = create_app(container)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE: _cookie(container.settings, user_id, guild_ids)},
    )


async def _premium(container, guild_id=GUILD):
    await container.entitlements.grant(guild_id, PlanTier.PREMIUM, None, OPERATOR)


async def test_create_draft_requires_premium(container):
    async with _client(container, MANAGER) as mgr:
        r = await mgr.post(f"/api/guilds/{GUILD}/persona/draft")
        assert r.status_code == 402  # без подписки нельзя


async def test_full_moderation_flow(container):
    await _premium(container)
    default_id = container.persona.default_id()
    async with _client(container, MANAGER) as mgr, _client(container, OPERATOR) as op:
        # создать черновик
        r = await mgr.post(f"/api/guilds/{GUILD}/persona/draft")
        assert r.status_code == 201
        draft_id = r.json()["draft_id"]
        assert draft_id is not None and r.json()["status"] == "draft"
        # ключевая гарантия: черновик НЕ назначен серверу — бот видит дефолт
        assert container.persona.assigned_persona_id(GUILD) == default_id
        # менеджер правит промпт своего черновика
        r = await mgr.put(f"/api/personas/{draft_id}/prompt", json={"prompt": "Я ворчливый кот."})
        assert r.status_code == 200
        # отправить на проверку
        r = await mgr.post(f"/api/guilds/{GUILD}/persona/draft/submit")
        assert r.status_code == 200 and r.json()["status"] == "pending"
        assert container.persona.assigned_persona_id(GUILD) == default_id  # всё ещё не назначен
        # оператор видит заявку в очереди
        r = await op.get("/api/persona-submissions")
        assert r.status_code == 200
        assert draft_id in [s["persona_id"] for s in r.json()]
        # одобрить → персона назначается серверу
        r = await op.post(f"/api/persona-submissions/{draft_id}/approve")
        assert r.status_code == 200
        assert container.persona.assigned_persona_id(GUILD) == draft_id
        # очередь опустела
        assert (await op.get("/api/persona-submissions")).json() == []


async def test_reject_returns_note_and_not_assigned(container):
    await _premium(container)
    default_id = container.persona.default_id()
    async with _client(container, MANAGER) as mgr, _client(container, OPERATOR) as op:
        draft_id = (await mgr.post(f"/api/guilds/{GUILD}/persona/draft")).json()["draft_id"]
        await mgr.post(f"/api/guilds/{GUILD}/persona/draft/submit")
        r = await op.post(
            f"/api/persona-submissions/{draft_id}/reject", json={"note": "слишком грубо"}
        )
        assert r.status_code == 204
        assert container.persona.assigned_persona_id(GUILD) == default_id  # не назначен
        state = (await mgr.get(f"/api/guilds/{GUILD}/persona/draft")).json()
        assert state["status"] == "rejected" and state["review_note"] == "слишком грубо"


async def test_outsider_cannot_edit_draft(container):
    await _premium(container)
    async with _client(container, MANAGER) as mgr:
        draft_id = (await mgr.post(f"/api/guilds/{GUILD}/persona/draft")).json()["draft_id"]
    # посторонний (админ другого сервера) не правит чужой черновик
    async with _client(container, OUTSIDER, guild_ids=(OTHER,)) as outsider:
        r = await outsider.put(f"/api/personas/{draft_id}/prompt", json={"prompt": "hack"})
        assert r.status_code == 403


async def test_manager_cannot_edit_approved_live_persona(container):
    await _premium(container)
    async with _client(container, MANAGER) as mgr, _client(container, OPERATOR) as op:
        draft_id = (await mgr.post(f"/api/guilds/{GUILD}/persona/draft")).json()["draft_id"]
        await mgr.post(f"/api/guilds/{GUILD}/persona/draft/submit")
        await op.post(f"/api/persona-submissions/{draft_id}/approve")
        # одобренную (approved, назначенную) персону менеджер уже не правит
        r = await mgr.put(f"/api/personas/{draft_id}/prompt", json={"prompt": "x"})
        assert r.status_code == 403


async def test_submission_not_in_operator_library(container):
    """Заявка сервера не засоряет операторскую библиотеку персон (только очередь)."""
    await _premium(container)
    async with _client(container, MANAGER) as mgr, _client(container, OPERATOR) as op:
        draft_id = (await mgr.post(f"/api/guilds/{GUILD}/persona/draft")).json()["draft_id"]
        lib = (await op.get("/api/personas")).json()
        assert draft_id not in [p["id"] for p in lib]
