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


# --- люди / отношения (список, карточка, admin-действия) --------------------


async def test_people_requires_session(container):
    app = create_app(container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        resp = await anon.get(f"/api/guilds/{GUILD}/people")
    assert resp.status_code == 401


async def test_people_list_detail_and_admin_actions(client, uow_factory, monkeypatch):
    from src.api.routers import people as people_router

    async def fake_users(_token, ids):
        return {uid: {"username": f"u{uid}", "avatar": None} for uid in ids}

    monkeypatch.setattr(people_router, "fetch_users", fake_users)
    async with uow_factory() as uow:
        p = await uow.relationships.get_or_create(7, GUILD)
        p.points = 500
        await uow.relationships.save(p)
        await uow.commit()

    # список
    lst = (await client.get(f"/api/guilds/{GUILD}/people")).json()
    assert any(e["user_id"] == "7" and e["points"] == 500 for e in lst)

    # карточка
    det = (await client.get(f"/api/guilds/{GUILD}/people/7")).json()
    assert det["points"] == 500 and det["frozen"] is False and det["username"] == "u7"

    # админ правит очки
    upd = await client.put(f"/api/guilds/{GUILD}/people/7/points", json={"value": 999})
    assert upd.status_code == 200 and upd.json()["points"] == 999

    # заморозка тоглится и видна в карточке
    fr = await client.post(f"/api/guilds/{GUILD}/people/7/freeze")
    assert fr.status_code == 200 and fr.json()["frozen"] is True
    det2 = (await client.get(f"/api/guilds/{GUILD}/people/7")).json()
    assert det2["frozen"] is True and det2["points"] == 999


# --- модерация (чтение банов/варнов + сброс варнов) -------------------------


async def test_moderation_requires_session(container):
    app = create_app(container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        resp = await anon.get(f"/api/guilds/{GUILD}/moderation/bans")
    assert resp.status_code == 401


async def test_moderation_bans_list(client, uow_factory, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from src.api.routers import moderation as mod_router
    from src.domain.moderation.entities import TempBan

    async def fake_users(_token, ids):
        return {uid: {"username": f"u{uid}", "avatar": None} for uid in ids}

    monkeypatch.setattr(mod_router, "fetch_users", fake_users)
    now = datetime.now(UTC)
    async with uow_factory() as uow:
        await uow.temp_bans.add(
            TempBan(
                guild_id=GUILD,
                user_id=42,
                moderator_id=1,
                reason="флуд",
                expires_at=now + timedelta(hours=2),
            )
        )
        # просроченный — не должен попасть в активные
        await uow.temp_bans.add(
            TempBan(
                guild_id=GUILD,
                user_id=43,
                moderator_id=1,
                reason="старьё",
                expires_at=now - timedelta(hours=1),
            )
        )
        await uow.commit()

    data = (await client.get(f"/api/guilds/{GUILD}/moderation/bans")).json()
    assert [b["user_id"] for b in data] == ["42"]
    assert data[0]["reason"] == "флуд" and data[0]["moderator_name"] == "u1"


async def test_moderation_warns_read_and_clear(client, uow_factory, monkeypatch):
    from datetime import UTC, datetime

    from src.api.routers import moderation as mod_router
    from src.domain.moderation.entities import Warn

    async def fake_users(_token, ids):
        return {uid: {"username": f"u{uid}", "avatar": None} for uid in ids}

    monkeypatch.setattr(mod_router, "fetch_users", fake_users)
    now = datetime.now(UTC)
    async with uow_factory() as uow:
        for reason in ("спам", "капс"):
            await uow.warns.add(
                Warn(guild_id=GUILD, user_id=9, moderator_id=1, reason=reason, created_at=now)
            )
        await uow.commit()

    warns = (await client.get(f"/api/guilds/{GUILD}/moderation/warns/9")).json()
    assert [w["reason"] for w in warns] == ["спам", "капс"]

    cleared = await client.delete(f"/api/guilds/{GUILD}/moderation/warns/9")
    assert cleared.status_code == 200 and cleared.json()["cleared"] == 2
    after = (await client.get(f"/api/guilds/{GUILD}/moderation/warns/9")).json()
    assert after == []


# --- музыка (read-модуль плейлистов) ---------------------------------------


async def test_music_requires_session(container):
    app = create_app(container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        resp = await anon.get(f"/api/guilds/{GUILD}/music/playlists")
    assert resp.status_code == 401


async def test_music_playlists_list_and_tracks(client, uow_factory, monkeypatch):
    from src.api.routers import music as music_router
    from src.domain.music.entities import Playlist, Track

    async def fake_users(_token, ids):
        return {uid: {"username": f"u{uid}", "avatar": None} for uid in ids}

    monkeypatch.setattr(music_router, "fetch_users", fake_users)
    async with uow_factory() as uow:
        await uow.playlists.save(
            Playlist(
                guild_id=GUILD,
                name="Ночь",
                created_by=3,
                tracks=[
                    Track(video_id="a", title="Трек A", url="u", duration=90, requested_by=3),
                    Track(video_id="b", title="Трек B", url="u", duration=None, requested_by=3),
                ],
            )
        )
        await uow.commit()

    lst = (await client.get(f"/api/guilds/{GUILD}/music/playlists")).json()
    assert lst == [{"name": "Ночь", "track_count": 2, "author_id": "3", "author_name": "u3"}]

    det = (await client.get(f"/api/guilds/{GUILD}/music/playlists/Ночь")).json()
    assert det["name"] == "Ночь"
    assert [t["title"] for t in det["tracks"]] == ["Трек A", "Трек B"]
    assert det["tracks"][0]["duration"] == 90 and det["tracks"][1]["duration"] is None


async def test_music_playlist_not_found_404(client):
    resp = await client.get(f"/api/guilds/{GUILD}/music/playlists/нет-такого")
    assert resp.status_code == 404


# --- находки (server-level обзор: активная + топ коллекционеров) ------------


async def test_finds_requires_session(container):
    app = create_app(container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        resp = await anon.get(f"/api/guilds/{GUILD}/finds/overview")
    assert resp.status_code == 401


async def test_finds_overview_empty(client, monkeypatch):
    from src.api.routers import finds as finds_router

    async def fake_users(_token, ids):
        return {}

    monkeypatch.setattr(finds_router, "fetch_users", fake_users)
    data = (await client.get(f"/api/guilds/{GUILD}/finds/overview")).json()
    assert data == {"active": None, "collectors": []}


async def test_finds_overview_collectors_ranked(client, uow_factory, monkeypatch):
    from datetime import UTC, datetime

    from src.api.routers import finds as finds_router
    from src.domain.finds.entities import CollectionItem

    async def fake_users(_token, ids):
        return {uid: {"username": f"u{uid}", "avatar": None} for uid in ids}

    monkeypatch.setattr(finds_router, "fetch_users", fake_users)
    now = datetime.now(UTC)
    async with uow_factory() as uow:
        # user 5: 3 находки, 1 подарена; user 6: 1 находка
        for _ in range(3):
            await uow.collections.add(
                CollectionItem(guild_id=GUILD, user_id=5, item_id="letter", obtained_at=now)
            )
        await uow.collections.add(
            CollectionItem(
                guild_id=GUILD, user_id=5, item_id="letter", obtained_at=now, gifted_at=now
            )
        )
        await uow.collections.add(
            CollectionItem(guild_id=GUILD, user_id=6, item_id="letter", obtained_at=now)
        )
        await uow.commit()

    data = (await client.get(f"/api/guilds/{GUILD}/finds/overview")).json()
    cols = data["collectors"]
    assert [c["user_id"] for c in cols] == ["5", "6"]  # по убыванию total
    assert cols[0]["total"] == 4 and cols[0]["gifted"] == 1
    assert cols[1]["total"] == 1 and cols[1]["gifted"] == 0
