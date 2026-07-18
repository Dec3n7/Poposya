"""API-панель, Ф2+Ф3: права доступа + редактор настроек сервера.

Права (можно ли управлять + есть ли бот на сервере) проверяет только бэкенд.
Настройки идут через тот же `GuildSettingsService`, что и `/config` — своей
бизнес-логики в API нет. БД — schema'нутый SQLite из conftest (session_factory).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.container import assemble_container
from src.api.security import SESSION_COOKIE, Session, SessionGuild, encode_session
from src.config import Settings

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
    # тот же сборщик, что и прод, но поверх schema'нутой тестовой БД из conftest
    c = assemble_container(make_settings(), session_factory.kw["bind"], session_factory)
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


# --- списочные/словарные настройки (роли, лимиты) через complex + batch ---


async def test_complex_settings_shape(client):
    resp = await client.get(f"/api/guilds/{GUILD}/settings/complex")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["role_thresholds"]["value"], list)
    assert isinstance(data["role_names"]["value"], list)
    assert isinstance(data["rate_limits"]["value"], dict)
    assert data["role_thresholds"]["is_override"] is False


async def test_batch_updates_roles_together(client):
    resp = await client.put(
        f"/api/guilds/{GUILD}/settings/batch",
        json={
            "values": {
                "relationship_role_thresholds": [50, 150],
                "relationship_role_names": ["a", "b", "c"],  # порогов+1
            }
        },
    )
    assert resp.status_code == 204
    data = (await client.get(f"/api/guilds/{GUILD}/settings/complex")).json()
    assert data["role_thresholds"]["value"] == [50, 150]
    assert data["role_names"]["value"] == ["a", "b", "c"]
    assert data["role_thresholds"]["is_override"] is True


async def test_batch_rejects_broken_invariant_422(client):
    # 2 порога требуют 3 имени; даём 2 -> кросс-инвариант падает, ничего не сохранено
    resp = await client.put(
        f"/api/guilds/{GUILD}/settings/batch",
        json={
            "values": {
                "relationship_role_thresholds": [50, 150],
                "relationship_role_names": ["a", "b"],
            }
        },
    )
    assert resp.status_code == 422
    data = (await client.get(f"/api/guilds/{GUILD}/settings/complex")).json()
    assert data["role_thresholds"]["is_override"] is False  # откат


async def test_batch_updates_rate_limits(client):
    resp = await client.put(
        f"/api/guilds/{GUILD}/settings/batch",
        json={"values": {"ai_rate_limits_by_level": {"1": 3, "2": 6, "3": 9, "4": 12, "5": 15, "6": 18, "7": 21}}},
    )
    assert resp.status_code == 204
    data = (await client.get(f"/api/guilds/{GUILD}/settings/complex")).json()
    assert data["rate_limits"]["value"]["1"] == 3


async def test_batch_rejects_unknown_key_404(client):
    resp = await client.put(
        f"/api/guilds/{GUILD}/settings/batch", json={"values": {"no_such_key": [1, 2]}}
    )
    assert resp.status_code == 404


# --- пикер каналов ----------------------------------------------------------


async def test_channels_returns_list(client, monkeypatch):
    from src.api.routers import guilds as guilds_router

    async def fake(_token, _gid):
        return [{"id": "111", "name": "общий", "group": "Текстовые", "position": 0}]

    monkeypatch.setattr(guilds_router, "fetch_guild_channels", fake)
    resp = await client.get(f"/api/guilds/{GUILD}/channels")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["name"] == "общий" and body[0]["id"] == "111"


async def test_channels_requires_session(container):
    app = create_app(container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        resp = await anon.get(f"/api/guilds/{GUILD}/channels")
    assert resp.status_code == 401


# --- дашборд (overview) -----------------------------------------------------


async def test_overview_requires_session(container):
    app = create_app(container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        resp = await anon.get(f"/api/guilds/{GUILD}/overview")
    assert resp.status_code == 401


async def test_overview_leaderboard_and_counts(client, uow_factory, monkeypatch):
    from src.api.routers import guilds as guilds_router

    async def fake_users(_token, ids):
        return {uid: {"username": f"user{uid}", "avatar": None} for uid in ids}

    monkeypatch.setattr(guilds_router, "fetch_users", fake_users)
    # два профиля с очками в этой гильдии
    async with uow_factory() as uow:
        for uid, pts in [(1, 300), (2, 100)]:
            p = await uow.relationships.get_or_create(uid, GUILD)
            p.points = pts
            await uow.relationships.save(p)
        await uow.commit()

    resp = await client.get(f"/api/guilds/{GUILD}/overview")
    assert resp.status_code == 200
    data = resp.json()
    board = data["leaderboard"]
    assert [e["points"] for e in board] == [300, 100]  # по убыванию очков
    assert board[0]["username"] == "user1"
    assert data["counts"] == {"watchlist": 0, "watched": 0, "playlists": 0}


# --- киноклуб ---------------------------------------------------------------


async def test_cinema_watchlist_and_watched(client, uow_factory):
    from datetime import UTC, datetime

    from src.domain.cinema.entities import MovieEntry

    now = datetime.now(UTC)
    async with uow_factory() as uow:
        await uow.movies.add(
            MovieEntry(guild_id=GUILD, title="Дюна", added_by=1, added_at=now, status="listed")
        )
        await uow.movies.add(
            MovieEntry(
                guild_id=GUILD,
                title="Бегущий",
                added_by=1,
                added_at=now,
                status="watched",
                avg_score=8.5,
                ratings_count=3,
            )
        )
        await uow.commit()

    resp = await client.get(f"/api/guilds/{GUILD}/cinema")
    assert resp.status_code == 200
    data = resp.json()
    assert any(m["title"] == "Дюна" for m in data["watchlist"])
    watched = data["watched"]
    assert any(m["title"] == "Бегущий" and m["avg_score"] == 8.5 for m in watched)
