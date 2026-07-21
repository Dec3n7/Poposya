"""API персон: CRUD/duplicate/prompt/assign/export-import под require_operator.

Только оператор (web_operator_ids) имеет доступ; серверный админ получает 403.
БД — schema'нутый SQLite из conftest; lifespan create_app поднимает дефолт-персону
(_ensure_default), поэтому список никогда не пуст."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.container import assemble_container
from src.api.security import SESSION_COOKIE, Session, SessionGuild, encode_session
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
    # в проде это делает lifespan create_app; httpx ASGITransport его не поднимает,
    # поэтому загружаем персоны (в т.ч. создаём дефолт-строку) явно — как прод-старт
    await c.persona.load_all()
    return c


def _cookie(settings, user_id: int) -> str:
    session = Session(
        user_id=user_id,
        username="u",
        avatar=None,
        guilds=[SessionGuild(id=GUILD, name="G", icon=None)],
    )
    return encode_session(settings.web_session_secret, session, 24)


@pytest.fixture
async def client(container):
    app = create_app(container)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE: _cookie(container.settings, OPERATOR)},
    ) as c:
        yield c


@pytest.fixture
async def stranger(container):
    app = create_app(container)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE: _cookie(container.settings, STRANGER)},
    ) as c:
        yield c


# --- доступ ------------------------------------------------------------------


async def test_requires_session(container):
    app = create_app(container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/personas")).status_code == 401


async def test_non_operator_forbidden(stranger):
    assert (await stranger.get("/api/personas")).status_code == 403
    assert (await stranger.post("/api/personas", json={"name": "X"})).status_code == 403


# --- CRUD --------------------------------------------------------------------


async def test_list_contains_default(client):
    resp = await client.get("/api/personas")
    assert resp.status_code == 200
    personas = resp.json()
    assert any(p["is_default"] and p["name"] == "Попося" for p in personas)


async def test_create_get_rename_delete(client):
    created = (await client.post("/api/personas", json={"name": "Резкий"})).json()
    pid = created["id"]
    assert created["name"] == "Резкий"
    assert not created["is_default"]

    got = (await client.get(f"/api/personas/{pid}")).json()
    assert got["name"] == "Резкий"

    renamed = (await client.patch(f"/api/personas/{pid}", json={"name": "Мягкий"})).json()
    assert renamed["name"] == "Мягкий"

    assert (await client.delete(f"/api/personas/{pid}")).status_code == 204
    assert (await client.get(f"/api/personas/{pid}")).status_code == 404


async def test_cannot_delete_default(client):
    default = next(p for p in (await client.get("/api/personas")).json() if p["is_default"])
    assert (await client.delete(f"/api/personas/{default['id']}")).status_code == 409


async def test_set_prompt_and_assign_takes_effect(client, container):
    pid = (await client.post("/api/personas", json={"name": "Свой"})).json()["id"]
    resp = await client.put(
        f"/api/personas/{pid}/prompt", json={"prompt": "Ты — {{name}}, отвечай сухо."}
    )
    assert resp.status_code == 200
    assert resp.json()["prompt"] == "Ты — {{name}}, отвечай сухо."

    assign = await client.put(f"/api/guilds/{GUILD}/persona", json={"persona_id": pid})
    assert assign.status_code == 200
    assert assign.json()["persona_id"] == pid

    # рантайм-резолв виден сразу (кэш перечитан после записи)
    assert container.persona.assigned_persona_id(GUILD) == pid
    assert container.persona.render_prompt(GUILD, {"name": "Кот"}) == "Ты — Кот, отвечай сухо."


async def test_assign_unknown_persona_404(client):
    assert (
        await client.put(f"/api/guilds/{GUILD}/persona", json={"persona_id": 999999})
    ).status_code == 404


async def test_duplicate_copies_prompt(client):
    base = (await client.post("/api/personas", json={"name": "База"})).json()
    await client.put(f"/api/personas/{base['id']}/prompt", json={"prompt": "БАЗОВЫЙ ПРОМПТ"})
    dup = (
        await client.post(f"/api/personas/{base['id']}/duplicate", json={"name": "Копия"})
    ).json()
    assert dup["name"] == "Копия"
    assert dup["prompt"] == "БАЗОВЫЙ ПРОМПТ"


async def test_export_import_roundtrip(client):
    src = (await client.post("/api/personas", json={"name": "Экспортируемый"})).json()
    await client.put(f"/api/personas/{src['id']}/prompt", json={"prompt": "ПЕРЕНОСИМЫЙ"})
    dump = (await client.get(f"/api/personas/{src['id']}/export")).json()
    assert dump["prompt"] == "ПЕРЕНОСИМЫЙ"

    imported = (await client.post("/api/personas/import", json=dump)).json()
    assert imported["id"] != src["id"]
    assert imported["prompt"] == "ПЕРЕНОСИМЫЙ"
    assert not imported["is_default"]


async def test_get_guild_persona_defaults_to_default(client, container):
    resp = await client.get(f"/api/guilds/{GUILD}/persona")
    assert resp.status_code == 200
    # без назначения возвращается дефолт-персона
    assert resp.json()["persona_id"] == container.persona.default_id()
