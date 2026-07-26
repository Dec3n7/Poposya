"""API-панель, Ф1: Discord OAuth2 + JWT-сессия.

Discord замокан (без реальной сети). Проверяем: редирект на Discord, отбой без
сессии, полный колбэк-флоу (кука сессии + /me с фильтром управляемых серверов),
защиту от подмены state, logout. Лайфспан не гоняем (ASGITransport) — этим
тестам БД не нужна.
"""

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from src.api import discord_oauth
from src.api.app import create_app
from src.api.container import build_api_container
from src.api.security import Session, decode_session, encode_session
from src.config import Settings


def make_settings(**over):
    base = {
        "discord_token": "t",
        "discord_client_id": "cid",
        "discord_client_secret": "csec",
        "web_session_secret": "test-session-secret-at-least-32-bytes!!",
        "web_oauth_redirect": "http://localhost:8081/api/auth/callback",
        "web_allowed_origin": "http://localhost:5173",
    }
    base.update(over)
    return Settings(_env_file=None, **base)


@pytest.fixture
def container():
    c = build_api_container(make_settings())
    c.bot_guilds.prime({10, 20})  # бот на серверах 10 и 20 (без похода в Discord)
    return c


@pytest.fixture
def app(container):
    return create_app(container)


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def mock_discord(monkeypatch):
    """Discord отдаёт юзера и 3 сервера: управляемый, свой (owner), и без прав."""

    async def fake_exchange(*_a, **_k):
        return "access-token"

    async def fake_identity(_token):
        user = {"id": "42", "username": "wild", "avatar": "av1"}
        guilds = [
            {"id": "10", "name": "Manage", "icon": "i10", "permissions": str(0x20)},
            {"id": "20", "name": "Owner", "icon": None, "owner": True, "permissions": "0"},
            {"id": "30", "name": "Nope", "icon": "i30", "permissions": "0"},
        ]
        return user, guilds

    monkeypatch.setattr(discord_oauth, "exchange_code", fake_exchange)
    monkeypatch.setattr(discord_oauth, "fetch_identity", fake_identity)


async def _login_get_state(client) -> str:
    resp = await client.get("/api/auth/login", follow_redirects=False)
    assert resp.status_code == 307
    return client.cookies["poposya_oauth_state"]


async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_login_redirects_to_discord(client):
    resp = await client.get("/api/auth/login", follow_redirects=False)
    assert resp.status_code == 307
    loc = resp.headers["location"]
    assert loc.startswith("https://discord.com/oauth2/authorize")
    assert "client_id=cid" in loc
    assert "scope=identify+guilds" in loc
    assert "poposya_oauth_state=" in resp.headers.get("set-cookie", "")


async def test_me_requires_session(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_callback_sets_session_and_me_filters_guilds(client, mock_discord):
    state = await _login_get_state(client)
    resp = await client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "http://localhost:5173"
    assert "poposya_session=" in resp.headers.get("set-cookie", "")

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    data = me.json()
    assert data["user_id"] == "42"
    assert data["username"] == "wild"
    # аватар/иконки отдаются готовыми CDN-URL, а не сырыми хэшами
    assert data["avatar"] == "https://cdn.discordapp.com/avatars/42/av1.png?size=64"
    # только управляемые серверы: manage (10) и owner (20), но не «без прав» (30)
    assert {g["id"] for g in data["guilds"]} == {"10", "20"}
    icons = {g["id"]: g["icon"] for g in data["guilds"]}
    assert icons["10"] == "https://cdn.discordapp.com/icons/10/i10.png?size=64"
    assert icons["20"] is None  # у owner-сервера иконки нет


async def test_me_hides_guilds_without_bot(mock_discord):
    # бот только на сервере 10 -> сервер 20 (owner) в /me не попадёт
    container = build_api_container(make_settings())
    container.bot_guilds.prime({10})
    app = create_app(container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/api/auth/login", follow_redirects=False)
        state = client.cookies["poposya_oauth_state"]
        await client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)
        me = await client.get("/api/auth/me")
    assert {g["id"] for g in me.json()["guilds"]} == {"10"}


async def test_callback_rejects_bad_state(client, mock_discord):
    await _login_get_state(client)
    resp = await client.get("/api/auth/callback?code=abc&state=forged", follow_redirects=False)
    assert resp.status_code == 400


async def test_callback_rejects_missing_code(client, mock_discord):
    state = await _login_get_state(client)
    resp = await client.get(f"/api/auth/callback?state={state}", follow_redirects=False)
    assert resp.status_code == 400


async def test_logout_clears_session(client, mock_discord):
    state = await _login_get_state(client)
    await client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)
    assert (await client.get("/api/auth/me")).status_code == 200

    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 204
    client.cookies.delete("poposya_session")  # сервер попросил удалить — эмулируем браузер
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_login_500_when_oauth_not_configured():
    app = create_app(
        build_api_container(make_settings(discord_client_id="", web_session_secret=""))
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/auth/login", follow_redirects=False)
        assert resp.status_code == 500


# --- серверный отзыв сессий через версию (web_session_version) ---------------

_SECRET = "test-session-secret-at-least-32-bytes!!"


def test_session_version_kill_switch():
    token = encode_session(_SECRET, Session(user_id=1, username="u", avatar=None, guilds=[]), 24, 1)
    assert decode_session(_SECRET, token, version=1) is not None
    # тот же секрет, но версия сессий поднята -> токен недействителен
    assert decode_session(_SECRET, token, version=2) is None


def test_legacy_token_without_sv_valid_at_v1():
    # токен, выпущенный до рубильника (без claim sv)
    payload = {"sub": "7", "username": "u", "avatar": None, "guilds": [], "exp": 9_999_999_999}
    legacy = jwt.encode(payload, _SECRET, algorithm="HS256")
    assert decode_session(_SECRET, legacy, version=1) is not None  # не гасим зря
    assert decode_session(_SECRET, legacy, version=2) is None  # но рубильник ловит


async def test_me_rejected_after_session_version_bump(mock_discord):
    c1 = build_api_container(make_settings(web_session_version=1))
    c1.bot_guilds.prime({10, 20})
    app1 = create_app(c1)
    async with AsyncClient(transport=ASGITransport(app=app1), base_url="http://test") as client:
        await client.get("/api/auth/login", follow_redirects=False)
        state = client.cookies["poposya_oauth_state"]
        await client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)
        assert (await client.get("/api/auth/me")).status_code == 200
        cookie = client.cookies["poposya_session"]

    # тот же секрет, но версия поднята на новом инстансе -> старая кука отвергнута
    c2 = build_api_container(make_settings(web_session_version=2))
    c2.bot_guilds.prime({10, 20})
    app2 = create_app(c2)
    async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as client2:
        client2.cookies.set("poposya_session", cookie)
        assert (await client2.get("/api/auth/me")).status_code == 401


# --- интерактивная схема FastAPI выключена по умолчанию (web_docs_enabled) ----


async def test_docs_disabled_by_default(client):
    assert (await client.get("/openapi.json")).status_code == 404
    assert (await client.get("/docs")).status_code == 404


async def test_docs_enabled_by_flag():
    app = create_app(build_api_container(make_settings(web_docs_enabled=True)))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/openapi.json")).status_code == 200


# --- fail-fast: слабый секрет сессий при включённой панели роняет старт --------


def test_weak_session_secret_rejected_when_panel_enabled():
    # панель включена (есть client_id), но секрет короткий -> Settings не соберётся
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            discord_token="t",
            discord_client_id="cid",
            discord_client_secret="csec",
            web_session_secret="short",
        )


def test_empty_session_secret_rejected_when_panel_enabled():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            discord_token="t",
            discord_client_id="cid",
            discord_client_secret="csec",
            web_session_secret="",
        )


def test_missing_client_secret_rejected_when_panel_enabled():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            discord_token="t",
            discord_client_id="cid",
            discord_client_secret="",
            web_session_secret="test-session-secret-at-least-32-bytes!!",
        )


def test_bot_only_profile_needs_no_web_secret():
    # чисто ботовый профиль (без client_id) валиден и с пустым секретом
    s = Settings(_env_file=None, discord_token="t")
    assert s.web_session_secret == ""
