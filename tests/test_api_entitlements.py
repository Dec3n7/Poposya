"""API тарифов: выдача/просмотр/снятие подписки под require_operator.

Только оператор (web_operator_ids); серверный админ/аноним — 403/401. БД —
schema'нутый SQLite из conftest."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.container import assemble_container
from src.api.security import SESSION_COOKIE, Session, SessionGuild, encode_session
from src.application.interfaces.entitlements import PlanTier
from src.config import Settings

GUILD = 10
OPERATOR = 1
STRANGER = 2


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
    await c.entitlements.load_all()
    return c


def _cookie(settings, user_id: int) -> str:
    session = Session(
        user_id=user_id,
        username="u",
        avatar=None,
        guilds=[SessionGuild(id=GUILD, name="G", icon=None)],
    )
    return encode_session(settings.web_session_secret, session, 24, settings.web_session_version)


@pytest.fixture
async def client(container):
    app = create_app(container)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE: _cookie(container.settings, OPERATOR)},
    ) as c:
        yield c


def _url(guild=GUILD):
    return f"/api/guilds/{guild}/entitlement"


# --- доступ ------------------------------------------------------------------


async def test_anon_401(container):
    app = create_app(container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get(_url())).status_code == 401


async def test_stranger_403(container):
    app = create_app(container)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE: _cookie(container.settings, STRANGER)},
    ) as stranger:
        assert (await stranger.get(_url())).status_code == 403
        assert (await stranger.put(_url(), json={"tier": "premium"})).status_code == 403


# --- чтение/выдача/снятие ----------------------------------------------------


async def test_get_default(client):
    r = await client.get(_url())
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "free"  # дефолт из коробки — free (enforcement включён)
    assert body["active"] is False
    assert body["default_tier"] == "free"
    assert body["enforced"] is True


async def test_grant_with_duration(client, container):
    r = await client.put(_url(), json={"tier": "premium", "duration_days": 30})
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "premium"
    assert body["active"] is True
    assert body["expires_at"] is not None
    # кэш сервиса (его читает бот) обновился
    assert container.entitlements.tier(GUILD) is PlanTier.PREMIUM


async def test_grant_permanent(client):
    r = await client.put(_url(), json={"tier": "pro", "duration_days": None})
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "pro" and body["expires_at"] is None and body["active"] is True


async def test_grant_invalid_tier(client):
    r = await client.put(_url(), json={"tier": "platinum"})
    assert r.status_code == 422


async def test_revoke(client, container):
    await client.put(_url(), json={"tier": "premium", "duration_days": 30})
    r = await client.delete(_url())
    assert r.status_code == 200
    assert r.json()["active"] is False
    assert container.entitlements.tier(GUILD) is container.entitlements.default_tier


async def test_enforced_flag_when_default_free(session_factory):
    settings = make_settings(entitlements_default_tier="free")
    c = assemble_container(settings, session_factory.kw["bind"], session_factory)
    c.bot_guilds.prime({GUILD})
    await c.entitlements.load_all()
    app = create_app(c)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE: _cookie(settings, OPERATOR)},
    ) as client:
        body = (await client.get(_url())).json()
        assert body["tier"] == "free" and body["enforced"] is True
