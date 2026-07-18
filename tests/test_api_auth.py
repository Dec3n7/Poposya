"""API-панель, Ф1: Discord OAuth2 + JWT-сессия.

Discord замокан (без реальной сети). Проверяем: редирект на Discord, отбой без
сессии, полный колбэк-флоу (кука сессии + /me с фильтром управляемых серверов),
защиту от подмены state, logout. Лайфспан не гоняем (ASGITransport) — этим
тестам БД не нужна.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import discord_oauth
from src.api.app import create_app
from src.api.container import build_api_container
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
def app():
    return create_app(build_api_container(make_settings()))


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
    # только управляемые серверы: manage (10) и owner (20), но не «без прав» (30)
    assert {g["id"] for g in data["guilds"]} == {"10", "20"}


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
    app = create_app(build_api_container(make_settings(discord_client_id="", web_session_secret="")))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/auth/login", follow_redirects=False)
        assert resp.status_code == 500
