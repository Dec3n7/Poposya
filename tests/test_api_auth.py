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
from src.api.security import (
    PERM_ADMINISTRATOR,
    PERM_BAN_MEMBERS,
    PERM_MANAGE_GUILD,
    PERM_MODERATE_MEMBERS,
    Session,
    SessionGuild,
    decode_session,
    encode_session,
)
from src.config import Settings
from src.infrastructure.db.models.base import Base


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
async def container(tmp_path):
    # logout/revoke пишут эпоху сессии в БД — нужна схема (изолированная на тест)
    settings = make_settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    c = build_api_container(settings)
    async with c.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    c.bot_guilds.prime({10, 20})  # бот на серверах 10 и 20 (без похода в Discord)
    yield c
    await c.engine.dispose()


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


async def test_me_exposes_per_guild_permissions(client, mock_discord):
    # фронт гасит недоступные действия по этим битам (гвард на бэке всё равно
    # стережёт — это лишь UX-подсказка)
    state = await _login_get_state(client)
    await client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)
    data = (await client.get("/api/auth/me")).json()
    perms = {g["id"]: g["perms"] for g in data["guilds"]}
    # сервер 10: только MANAGE_GUILD -> вход в панель есть, конкретных прав нет
    assert perms["10"] == {
        "can_ban": False,
        "can_kick": False,
        "can_moderate": False,
        "can_manage_roles": False,
    }
    # сервер 20: владелец -> ADMINISTRATOR покрывает всё
    assert perms["20"] == {
        "can_ban": True,
        "can_kick": True,
        "can_moderate": True,
        "can_manage_roles": True,
    }


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


# --- пер-действие права Discord в сессии (F1) --------------------------------


def test_session_roundtrip_preserves_permissions():
    perms = PERM_MANAGE_GUILD | PERM_BAN_MEMBERS
    sess = Session(
        user_id=1,
        username="u",
        avatar=None,
        guilds=[SessionGuild(id=10, name="G", icon=None, permissions=perms)],
    )
    token = encode_session(_SECRET, sess, 24, 1)
    decoded = decode_session(_SECRET, token, 1)
    assert decoded is not None
    assert decoded.guilds[0].permissions == perms


def test_has_permission_specific_bit():
    sess = Session(
        user_id=1,
        username="u",
        avatar=None,
        guilds=[
            SessionGuild(
                id=10, name="G", icon=None, permissions=PERM_MANAGE_GUILD | PERM_BAN_MEMBERS
            )
        ],
    )
    assert sess.has_permission(10, PERM_BAN_MEMBERS) is True
    assert sess.has_permission(10, PERM_MODERATE_MEMBERS) is False  # права нет
    assert sess.has_permission(999, PERM_BAN_MEMBERS) is False  # чужой сервер


def test_has_permission_administrator_covers_all():
    sess = Session(
        user_id=1,
        username="u",
        avatar=None,
        guilds=[SessionGuild(id=10, name="G", icon=None, permissions=PERM_ADMINISTRATOR)],
    )
    assert sess.has_permission(10, PERM_BAN_MEMBERS) is True
    assert sess.has_permission(10, PERM_MODERATE_MEMBERS) is True


def test_has_permission_legacy_none_is_fail_closed():
    # токен без записанных битов (легаси) -> прав НЕТ (fail-closed). Раньше None
    # означал «всё можно» — это была дыра; такие токены гасит и рубильник версии.
    sess = Session(
        user_id=1,
        username="u",
        avatar=None,
        guilds=[SessionGuild(id=10, name="G", icon=None)],  # permissions=None
    )
    assert sess.has_permission(10, PERM_BAN_MEMBERS) is False


# --- скользящий тайм-аут бездействия (idle session) --------------------------

import time as _time  # noqa: E402


def _exp_of(token: str) -> int:
    return int(jwt.decode(token, _SECRET, algorithms=["HS256"])["exp"])


def test_idle_window_shortens_exp():
    # idle=15 мин при потолке 24 ч -> exp ~ окно бездействия, а не сутки
    sess = Session(user_id=1, username="u", avatar=None, guilds=[])
    token = encode_session(_SECRET, sess, 24, 2, idle_minutes=15)
    now = int(_time.time())
    exp = _exp_of(token)
    assert 800 <= exp - now <= 15 * 60 + 5  # около 15 минут, не 24 часа


def test_idle_disabled_keeps_absolute_ttl():
    # idle=0 -> прежнее поведение: exp = now + ttl_hours
    sess = Session(user_id=1, username="u", avatar=None, guilds=[])
    token = encode_session(_SECRET, sess, 24, 2)  # idle_minutes по умолчанию 0
    now = int(_time.time())
    assert (24 * 3600) - 5 <= _exp_of(token) - now <= 24 * 3600 + 5


def test_absolute_cap_clamps_sliding_exp():
    # начало сессии почти сутки назад: продление НЕ уводит exp за потолок 24 ч
    sess = Session(user_id=1, username="u", avatar=None, guilds=[])
    start = int(_time.time()) - (24 * 3600 - 60)  # до потолка ~60 сек
    token = encode_session(_SECRET, sess, 24, 2, idle_minutes=15, session_start=start)
    now = int(_time.time())
    assert _exp_of(token) - now <= 65  # зажато потолком, а не 15 минут


def test_expired_idle_token_is_rejected():
    # токен с истёкшим exp (простой дольше idle) -> сервер отвергает
    payload = {
        "sub": "1",
        "username": "u",
        "avatar": None,
        "guilds": [],
        "sv": 2,
        "ep": 0,
        "iat0": int(_time.time()) - 3600,
        "iat": int(_time.time()) - 3600,
        "exp": int(_time.time()) - 10,
    }
    expired = jwt.encode(payload, _SECRET, algorithm="HS256")
    assert decode_session(_SECRET, expired, version=2) is None


def test_issued_at_roundtrips_for_absolute_cap():
    start = int(_time.time()) - 5000
    sess = Session(user_id=1, username="u", avatar=None, guilds=[])
    token = encode_session(_SECRET, sess, 24, 2, idle_minutes=15, session_start=start)
    decoded = decode_session(_SECRET, token, version=2)
    assert decoded is not None and decoded.issued_at == start


async def test_me_refreshes_session_cookie(client, mock_discord):
    # каждый авторизованный запрос перевыпускает куку (скользящее продление)
    state = await _login_get_state(client)
    await client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)
    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert "poposya_session=" in me.headers.get("set-cookie", "")


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


# --- F6: серверный отзыв сессий (эпоха на пользователя) ----------------------


async def _login(client) -> None:
    state = await _login_get_state(client)
    await client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)


async def test_logout_revokes_session_server_side(client, mock_discord):
    await _login(client)
    old = client.cookies["poposya_session"]  # копия токена (как утёкший/сохранённый)
    assert (await client.get("/api/auth/me")).status_code == 200

    assert (await client.post("/api/auth/logout")).status_code == 204
    # даже с тем же токеном (logout лишь удалил куку в браузере) — сервер отверг
    client.cookies.set("poposya_session", old)
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_login_after_revoke_still_works(client, mock_discord):
    await _login(client)
    await client.post("/api/auth/logout")  # эпоха +1 — прежние токены мертвы
    await _login(client)  # свежий вход выдаёт токен с новой эпохой
    assert (await client.get("/api/auth/me")).status_code == 200


async def test_revoke_requires_operator(client, mock_discord):
    await _login(client)  # 42 вошёл, но он НЕ в web_operator_ids
    assert (await client.post("/api/auth/revoke/99")).status_code == 403


async def test_operator_revoke_kills_user_sessions(tmp_path, mock_discord):
    c = build_api_container(
        make_settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'op.db'}",
            web_operator_ids=[42],  # 42 — оператор бота
        )
    )
    async with c.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    c.bot_guilds.prime({10, 20})
    app = create_app(c)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _login(client)
            assert (await client.get("/api/auth/me")).status_code == 200
            # оператор отзывает все сессии пользователя 42
            assert (await client.post("/api/auth/revoke/42")).status_code == 204
            assert (await client.get("/api/auth/me")).status_code == 401
    finally:
        await c.engine.dispose()


# --- CSRF: Origin/Referer guard на state-changing запросах -------------------


async def test_csrf_blocks_cross_site_origin(client, mock_discord):
    await _login(client)
    resp = await client.post("/api/auth/logout", headers={"origin": "http://evil.example"})
    assert resp.status_code == 403


async def test_csrf_blocks_cross_site_referer(client, mock_discord):
    await _login(client)
    resp = await client.post("/api/auth/logout", headers={"referer": "http://evil.example/attack"})
    assert resp.status_code == 403


async def test_csrf_allows_matching_origin(client, mock_discord):
    await _login(client)
    # свой Origin -> кроме совпадения требуется валидный double-submit токен
    csrf = client.cookies["poposya_csrf"]
    resp = await client.post(
        "/api/auth/logout",
        headers={"origin": "http://localhost:5173", "X-CSRF-Token": csrf},
    )
    assert resp.status_code == 204


async def test_csrf_token_required_when_origin_present(client, mock_discord):
    # свой Origin, но БЕЗ токена -> отбой (double-submit не выполнен)
    await _login(client)
    resp = await client.post("/api/auth/logout", headers={"origin": "http://localhost:5173"})
    assert resp.status_code == 403


async def test_csrf_token_rejected_when_forged(client, mock_discord):
    await _login(client)
    resp = await client.post(
        "/api/auth/logout",
        headers={"origin": "http://localhost:5173", "X-CSRF-Token": "deadbeef"},
    )
    assert resp.status_code == 403


async def test_csrf_cookie_set_on_login(client, mock_discord):
    # колбэк выдаёт НЕ-httpOnly куку с CSRF-токеном (её читает фронт)
    state = await _login_get_state(client)
    resp = await client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)
    set_cookie = resp.headers.get("set-cookie", "")
    assert "poposya_csrf=" in set_cookie


async def test_csrf_allows_absent_origin(client, mock_discord):
    # сервер-сервер / curl / тест без Origin — не браузерный CSRF-вектор, кука Lax
    await _login(client)
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 204


async def test_csrf_ignores_safe_methods(client, mock_discord):
    # GET с чужим Origin не режем — он без побочных эффектов
    await _login(client)
    resp = await client.get("/api/auth/me", headers={"origin": "http://evil.example"})
    assert resp.status_code == 200


# --- TTL снимка прав Discord (web_perm_ttl_minutes) --------------------------

from types import SimpleNamespace  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from src.api.dependencies import assert_perms_fresh  # noqa: E402


def test_perms_at_roundtrips():
    sess = Session(user_id=1, username="u", avatar=None, guilds=[])
    start = int(_time.time()) - 9000
    token = encode_session(_SECRET, sess, 24, 2, idle_minutes=15, perms_at=start)
    decoded = decode_session(_SECRET, token, version=2)
    assert decoded is not None and decoded.perms_at == start


def test_legacy_token_without_pat_falls_back_to_session_start():
    # токен без claim pat -> возраст снимка прав считаем от начала сессии (iat0)
    start = int(_time.time()) - 100
    payload = {
        "sub": "1",
        "username": "u",
        "avatar": None,
        "guilds": [],
        "sv": 2,
        "ep": 0,
        "iat0": start,
        "iat": start,
        "exp": int(_time.time()) + 3600,
    }
    token = jwt.encode(payload, _SECRET, algorithm="HS256")
    decoded = decode_session(_SECRET, token, version=2)
    assert decoded is not None and decoded.perms_at == start


def _perm_ctx(ttl_minutes: int, perms_age_seconds: int, method: str = "POST"):
    request = SimpleNamespace(method=method)
    container = SimpleNamespace(settings=SimpleNamespace(web_perm_ttl_minutes=ttl_minutes))
    session = Session(
        user_id=1,
        username="u",
        avatar=None,
        guilds=[],
        perms_at=int(_time.time()) - perms_age_seconds,
    )
    return request, container, session


def test_perms_fresh_disabled_by_default():
    # ttl=0 -> проверка выключена, старый снимок проходит
    assert_perms_fresh(*_perm_ctx(ttl_minutes=0, perms_age_seconds=10_000))  # не бросает


def test_perms_fresh_allows_recent_snapshot():
    assert_perms_fresh(*_perm_ctx(ttl_minutes=30, perms_age_seconds=60))  # свежий -> ок


def test_perms_fresh_rejects_stale_write():
    with pytest.raises(HTTPException) as exc:
        assert_perms_fresh(*_perm_ctx(ttl_minutes=30, perms_age_seconds=31 * 60))
    assert exc.value.status_code == 401


def test_perms_fresh_ignores_safe_methods():
    # чтение (GET) не гейтим свежестью прав, даже если снимок протух
    assert_perms_fresh(*_perm_ctx(ttl_minutes=30, perms_age_seconds=10_000, method="GET"))


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
