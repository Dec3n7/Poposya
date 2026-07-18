"""API-панель, Ф2+Ф3: права доступа + редактор настроек сервера.

Права (можно ли управлять + есть ли бот на сервере) проверяет только бэкенд.
Настройки идут через тот же `GuildSettingsService`, что и `/config` — своей
бизнес-логики в API нет. БД — schema'нутый SQLite из conftest (session_factory).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.bot_guilds import BotGuildsCache
from src.api.container import ApiContainer
from src.api.security import SESSION_COOKIE, Session, SessionGuild, encode_session
from src.config import Settings
from src.infrastructure.guild_settings import GuildSettingsService

GUILD = 10


def make_settings(**over):
    base = {
        "discord_token": "t",
        "discord_client_id": "cid",
        "discord_client_secret": "csec",
        "web_session_secret": "test-session-secret-at-least-32-bytes!!",
    }
    base.update(over)
    return Settings(_env_file=None, **base)


@pytest.fixture
def container(session_factory):
    settings = make_settings()
    gs = GuildSettingsService(settings, session_factory)
    engine = session_factory.kw["bind"]
    c = ApiContainer(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        guild_settings=gs,
        bot_guilds=BotGuildsCache(""),
        settings_listener=None,
    )
    c.bot_guilds.prime({GUILD})  # бот «стоит» на сервере 10 (без похода в Discord)
    return c


def _cookie(settings, manage_guild_ids) -> str:
    session = Session(
        user_id=1,
        username="u",
        avatar=None,
        guilds=[SessionGuild(id=g, name="G", icon=None) for g in manage_guild_ids],
    )
    return encode_session(settings.web_session_secret, session, 24)


@pytest.fixture
async def client(container):
    app = create_app(container)
    token = _cookie(container.settings, {GUILD})  # управляет сервером 10
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE: token},
    ) as c:
        yield c


# --- права ------------------------------------------------------------------


async def test_settings_requires_session(container):
    app = create_app(container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        resp = await anon.get(f"/api/guilds/{GUILD}/settings")
    assert resp.status_code == 401


async def test_settings_forbidden_when_cannot_manage(container):
    app = create_app(container)
    token = _cookie(container.settings, {999})  # управляет другим сервером, не 10
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={SESSION_COOKIE: token}
    ) as c:
        resp = await c.get(f"/api/guilds/{GUILD}/settings")
    assert resp.status_code == 403


async def test_settings_404_when_bot_absent(container):
    container.bot_guilds.prime({777})  # бота НЕТ на сервере 10
    app = create_app(container)
    token = _cookie(container.settings, {GUILD})
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={SESSION_COOKIE: token}
    ) as c:
        resp = await c.get(f"/api/guilds/{GUILD}/settings")
    assert resp.status_code == 404


# --- чтение схемы + значений ------------------------------------------------


async def test_list_settings_returns_fields_with_defaults(client):
    resp = await client.get(f"/api/guilds/{GUILD}/settings")
    assert resp.status_code == 200
    fields = {f["key"]: f for f in resp.json()}
    warn = fields["warn_threshold"]
    assert warn["kind"] == "int"
    assert warn["default"] == 3 and warn["value"] == 3
    assert warn["is_override"] is False
    assert warn["min"] == 1 and warn["max"] == 20


# --- запись / сброс ---------------------------------------------------------


async def test_put_setting_overrides_and_notifies(client):
    resp = await client.put(f"/api/guilds/{GUILD}/settings/warn_threshold", json={"value": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["value"] == 5 and body["is_override"] is True
    # переопределение видно и при следующем чтении
    fields = {f["key"]: f for f in (await client.get(f"/api/guilds/{GUILD}/settings")).json()}
    assert fields["warn_threshold"]["value"] == 5


async def test_put_bool_and_channel(client):
    r1 = await client.put(f"/api/guilds/{GUILD}/settings/staykick_enabled", json={"value": True})
    assert r1.status_code == 200 and r1.json()["value"] is True
    r2 = await client.put(
        f"/api/guilds/{GUILD}/settings/cinema_forum_channel", json={"value": "123456789012345678"}
    )
    assert r2.status_code == 200 and r2.json()["value"] == "123456789012345678"  # строкой


async def test_put_invalid_value_422(client):
    resp = await client.put(f"/api/guilds/{GUILD}/settings/warn_threshold", json={"value": 999})
    assert resp.status_code == 422  # вне диапазона 1..20


async def test_put_unknown_key_404(client):
    resp = await client.put(f"/api/guilds/{GUILD}/settings/no_such_key", json={"value": 1})
    assert resp.status_code == 404


async def test_delete_resets_to_default(client):
    await client.put(f"/api/guilds/{GUILD}/settings/lonely_hours", json={"value": 6})
    resp = await client.delete(f"/api/guilds/{GUILD}/settings/lonely_hours")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_override"] is False
    assert body["value"] == make_settings().lonely_hours  # вернулся дефолт
