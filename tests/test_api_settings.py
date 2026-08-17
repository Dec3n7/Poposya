"""API-панель, Ф2+Ф3: права доступа + редактор настроек сервера.

Права (можно ли управлять + есть ли бот на сервере) проверяет только бэкенд.
Настройки идут через тот же `GuildSettingsService`, что и `/config` — своей
бизнес-логики в API нет. БД — schema'нутый SQLite из conftest (session_factory).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.container import assemble_container
from src.api.security import (
    PERM_ADMINISTRATOR,
    SESSION_COOKIE,
    Session,
    SessionGuild,
    encode_session,
)
from src.config import Settings

GUILD = 10


def make_settings(**over):
    base = {
        "discord_token": "t",
        "discord_client_id": "cid",
        "discord_client_secret": "csec",
        "web_session_secret": "test-session-secret-at-least-32-bytes!!",
        "web_command_wait_seconds": 2.0,  # мост: не ждать по 5 с в тестах
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
    # ADMINISTRATOR: дефолтный клиент — полноправный админ сервера (пер-действие
    # гейты проверяются отдельно явными масками). None означал бы «прав нет»
    # (fail-closed) и валил бы негейтовые тесты 403-й.
    session = Session(
        user_id=1,
        username="u",
        avatar=None,
        guilds=[
            SessionGuild(id=g, name="G", icon=None, permissions=PERM_ADMINISTRATOR)
            for g in manage_guild_ids
        ],
    )
    return encode_session(settings.web_session_secret, session, 24, settings.web_session_version)


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
        json={
            "values": {
                "ai_rate_limits_by_level": {
                    "1": 3,
                    "2": 6,
                    "3": 9,
                    "4": 12,
                    "5": 15,
                    "6": 18,
                    "7": 21,
                }
            }
        },
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

    async def fake_presence(_token, _gid):
        return 3

    monkeypatch.setattr(guilds_router, "fetch_users", fake_users)
    monkeypatch.setattr(guilds_router, "fetch_guild_presence", fake_presence)
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
    # role_index пробрасывается (пороги [100,250,...]): 300 -> тир 1, 100 -> тир 0
    assert board[0]["role_index"] == 1 and board[1]["role_index"] == 0
    # распределение по ролям для пончика: по одному человеку в тирах 0 и 1
    dist = {d["index"]: d["count"] for d in data["distribution"]}
    assert dist == {0: 1, 1: 1}
    assert all("name" in d for d in data["distribution"])
    assert data["counts"] == {"watchlist": 0, "watched": 0, "playlists": 0}
    assert data["online"] == 3  # approximate_presence_count из Discord (замокан)
    assert data["voice"] == [] and data["birthdays"] == []  # пусто без активности
    assert data["newcomers"] is None  # роль-новичок не настроена по умолчанию


async def test_overview_newcomers_count(client, container, monkeypatch):
    """Когда роль-новичок настроена — «Обзор» отдаёт число её носителей из
    зеркала ролей (для оценки «сколько новеньких»)."""
    from types import SimpleNamespace

    from src.api.routers import guilds as guilds_router

    async def fake_users(_token, ids):
        return {}

    async def fake_presence(_token, _gid):
        return None

    async def fake_roles(_gid):
        # (роли, meta, счётчики носителей по role_id) — поле сущности GuildRole.role_id
        return ([SimpleNamespace(role_id=777, name="🌫️ Смутный силуэт")], None, {777: 4})

    monkeypatch.setattr(guilds_router, "fetch_users", fake_users)
    monkeypatch.setattr(guilds_router, "fetch_guild_presence", fake_presence)
    monkeypatch.setattr(container.list_roles, "execute", fake_roles)
    await container.guild_settings.set(GUILD, "relationship_newcomer_role", "🌫️ Смутный силуэт")

    data = (await client.get(f"/api/guilds/{GUILD}/overview")).json()
    assert data["newcomers"] == {"name": "🌫️ Смутный силуэт", "count": 4}


async def test_overview_voice_and_birthdays(client, uow_factory, monkeypatch):
    from datetime import UTC, datetime

    from src.api.routers import guilds as guilds_router

    async def fake_users(_token, ids):
        return {uid: {"username": f"user{uid}", "avatar": None} for uid in ids}

    async def fake_presence(_token, _gid):
        return None

    monkeypatch.setattr(guilds_router, "fetch_users", fake_users)
    monkeypatch.setattr(guilds_router, "fetch_guild_presence", fake_presence)
    async with uow_factory() as uow:
        # войс: два часа юзеру 5 (total_minutes += accrued)
        await uow.voice_progress.save_many({(GUILD, 5): 0.0}, accrued_minutes=120.0)
        # ДР юзеру 7 — сегодня (in_days == 0)
        p = await uow.relationships.get_or_create(7, GUILD)
        today = datetime.now(UTC).date()
        p.birthday_day = today.day
        p.birthday_month = today.month
        await uow.relationships.save(p)
        await uow.commit()

    data = (await client.get(f"/api/guilds/{GUILD}/overview")).json()
    assert data["voice"] == [{"user_id": "5", "username": "user5", "avatar": None, "hours": 2.0}]
    bd = data["birthdays"]
    assert len(bd) == 1 and bd[0]["user_id"] == "7" and bd[0]["in_days"] == 0


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
    # id отдаётся для раскрытия деталей
    assert all("id" in m for m in watched)


async def test_cinema_movie_ratings(client, uow_factory, monkeypatch):
    from datetime import UTC, datetime

    from src.api.routers import guilds as guilds_router
    from src.domain.cinema.entities import MovieEntry

    async def fake_users(_token, ids):
        return {uid: {"username": f"user{uid}", "avatar": None} for uid in ids}

    monkeypatch.setattr(guilds_router, "fetch_users", fake_users)

    now = datetime.now(UTC)
    async with uow_factory() as uow:
        entry = await uow.movies.add(
            MovieEntry(guild_id=GUILD, title="Бегущий", added_by=1, added_at=now, status="watched")
        )
        await uow.movie_ratings.upsert(entry.id, 1, 9, now)  # только балл
        await uow.movie_ratings.set_review(entry.id, 2, "шедевр", now)  # только рецензия
        await uow.commit()

    resp = await client.get(f"/api/guilds/{GUILD}/cinema/movies/{entry.id}")
    assert resp.status_code == 200
    ratings = resp.json()["ratings"]
    by_user = {r["user_id"]: r for r in ratings}
    assert by_user["1"]["score"] == 9 and by_user["1"]["username"] == "user1"
    assert by_user["2"]["review"] == "шедевр"


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

    async def fake_members(_token, _gid, cap=2000):
        # участник 7 — с профилем; участник 8 — «зашёл, но с ботом не говорил»
        return [
            {"user_id": 7, "name": "u7", "avatar": None},
            {"user_id": 8, "name": "lurker", "avatar": None},
        ]

    monkeypatch.setattr(people_router, "fetch_users", fake_users)
    monkeypatch.setattr(people_router, "fetch_guild_members", fake_members)
    async with uow_factory() as uow:
        p = await uow.relationships.get_or_create(7, GUILD)
        p.points = 500
        await uow.relationships.save(p)
        await uow.commit()

    # список: все участники сервера, смерженные с профилями
    lst = {e["user_id"]: e for e in (await client.get(f"/api/guilds/{GUILD}/people")).json()}
    assert lst["7"]["points"] == 500 and lst["7"]["has_profile"] is True
    # role_index пробрасывается: 500 очков при порогах [100,250,450,...] -> тир 2
    assert lst["7"]["role_index"] == 2
    # прогресс до след. роли: next=700, доля (500-450)/(700-450)=0.2
    assert lst["7"]["next_threshold"] == 700
    assert abs(lst["7"]["role_progress"] - 0.2) < 1e-6
    # участник без профиля — в списке, но пустой
    assert lst["8"]["points"] == 0 and lst["8"]["has_profile"] is False
    assert lst["8"]["role"] is None and lst["8"]["frozen"] is False
    assert lst["8"]["role_index"] is None

    # карточка
    det = (await client.get(f"/api/guilds/{GUILD}/people/7")).json()
    assert det["points"] == 500 and det["frozen"] is False and det["username"] == "u7"
    assert det["role_index"] == 2

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


async def test_moderation_guild_warns_counts(client, uow_factory, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from src.api.routers import moderation as mod_router
    from src.domain.moderation.entities import Warn

    async def fake_users(_token, ids):
        return {uid: {"username": f"u{uid}", "avatar": None} for uid in ids}

    monkeypatch.setattr(mod_router, "fetch_users", fake_users)
    now = datetime.now(UTC)
    async with uow_factory() as uow:
        # user 9 — два варна (последний свежее), user 8 — один
        await uow.warns.add(
            Warn(
                guild_id=GUILD,
                user_id=9,
                moderator_id=1,
                reason="a",
                created_at=now - timedelta(days=2),
            )
        )
        await uow.warns.add(
            Warn(guild_id=GUILD, user_id=9, moderator_id=1, reason="b", created_at=now)
        )
        await uow.warns.add(
            Warn(
                guild_id=GUILD,
                user_id=8,
                moderator_id=1,
                reason="c",
                created_at=now - timedelta(days=1),
            )
        )
        await uow.commit()

    rows = (await client.get(f"/api/guilds/{GUILD}/moderation/warns")).json()
    # по убыванию числа варнов: сначала user 9 (2), затем user 8 (1)
    assert [(r["user_id"], r["count"]) for r in rows] == [("9", 2), ("8", 1)]
    assert rows[0]["username"] == "u9"


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


# --- командный мост панель→бот (модерация + музыка write) -------------------


async def _fake_bot(container, execute):
    """Фоновый «бот»: крутит process_pending с подставным Discord-исполнителем.
    Возвращает (task, stop, seen)."""
    import asyncio

    from src.infrastructure.commands.bridge import CommandProcessor

    seen = []

    async def executor(cmd):
        seen.append(cmd)
        return await execute(cmd)

    proc = CommandProcessor(container.session_factory, executor)
    stop = asyncio.Event()

    async def pump():
        while not stop.is_set():
            await proc.process_pending()
            await asyncio.sleep(0.01)

    return asyncio.create_task(pump()), stop, seen


async def test_moderation_ban_requires_session(container):
    app = create_app(container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        resp = await anon.post(
            f"/api/guilds/{GUILD}/moderation/ban", json={"user_id": "1", "minutes": 5}
        )
    assert resp.status_code == 401


async def test_moderation_ban_command_roundtrip(client, container):
    async def execute(_cmd):
        return "Забанен до 2026 г."

    task, stop, seen = await _fake_bot(container, execute)
    try:
        resp = await client.post(
            f"/api/guilds/{GUILD}/moderation/ban",
            json={"user_id": "42", "minutes": 60, "reason": "спам"},
        )
    finally:
        stop.set()
        await task
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "done" and "Забанен" in data["result"]
    assert seen[0].command_type == "mod.tempban"
    assert seen[0].payload == {"user_id": "42", "minutes": 60, "reason": "спам"}


async def test_moderation_command_failure_surfaces(client, container):
    from src.infrastructure.commands.bridge import CommandError

    async def execute(_cmd):
        raise CommandError("Нет права Ban Members.")

    task, stop, _seen = await _fake_bot(container, execute)
    try:
        resp = await client.post(f"/api/guilds/{GUILD}/moderation/unban", json={"user_id": "42"})
    finally:
        stop.set()
        await task
    data = resp.json()
    assert resp.status_code == 200  # ожидаемый провал — не HTTP-ошибка
    assert data["status"] == "failed" and data["result"] == "Нет права Ban Members."


async def test_moderation_ban_validation_422(client):
    resp = await client.post(
        f"/api/guilds/{GUILD}/moderation/ban", json={"user_id": "42", "minutes": 0}
    )
    assert resp.status_code == 422  # minutes >= 1


async def test_music_control_command(client, container):
    async def execute(_cmd):
        return "Пауза."

    task, stop, seen = await _fake_bot(container, execute)
    try:
        resp = await client.post(f"/api/guilds/{GUILD}/music/control", json={"action": "pause"})
    finally:
        stop.set()
        await task
    data = resp.json()
    assert resp.status_code == 200 and data["status"] == "done"
    assert seen[0].command_type == "music.pause"


async def test_music_control_bad_action_422(client):
    resp = await client.post(f"/api/guilds/{GUILD}/music/control", json={"action": "boom"})
    assert resp.status_code == 422


# --- тренды дашборда --------------------------------------------------------


async def test_overview_trends_returns_series(client, container):
    from datetime import UTC, datetime, timedelta

    from src.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
    from src.infrastructure.events.in_memory_bus import InMemoryEventBus

    today = datetime.now(UTC).date()
    async with SqlAlchemyUnitOfWork(container.session_factory, InMemoryEventBus()) as uow:
        await uow.metrics.record(GUILD, today - timedelta(days=1), {"members": 10})
        await uow.metrics.record(GUILD, today, {"members": 12})
        await uow.commit()

    resp = await client.get(f"/api/guilds/{GUILD}/overview/trends?days=30")
    assert resp.status_code == 200
    members = resp.json()["members"]
    # сериализуется как [день-ISO, число], старые -> новые
    assert members[0] == [(today - timedelta(days=1)).isoformat(), 10.0]
    assert members[-1] == [today.isoformat(), 12.0]


async def test_overview_trends_empty_when_no_snapshots(client):
    resp = await client.get(f"/api/guilds/{GUILD}/overview/trends")
    assert resp.status_code == 200
    assert resp.json() == {}


async def test_overview_trends_requires_manage(container):
    app = create_app(container)
    token = _cookie(container.settings, {999})  # не управляет сервером 10
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={SESSION_COOKIE: token}
    ) as c:
        resp = await c.get(f"/api/guilds/{GUILD}/overview/trends")
    assert resp.status_code == 403


async def test_summary_badge_counts(client, uow_factory):
    """Счётчики для бейджей: активные баны, заморозки, участники с варнами."""
    from datetime import UTC, datetime, timedelta

    from src.domain.moderation.entities import TempBan, Warn

    now = datetime.now(UTC)
    async with uow_factory() as uow:
        # заморожённый профиль
        p = await uow.relationships.get_or_create(7, GUILD)
        p.frozen_by_admin = True
        await uow.relationships.save(p)
        # активный бан + просроченный (не считается)
        await uow.temp_bans.add(
            TempBan(
                guild_id=GUILD,
                user_id=42,
                moderator_id=1,
                reason="x",
                expires_at=now + timedelta(hours=1),
            )
        )
        await uow.temp_bans.add(
            TempBan(
                guild_id=GUILD,
                user_id=43,
                moderator_id=1,
                reason="old",
                expires_at=now - timedelta(hours=1),
            )
        )
        # варн участнику
        await uow.warns.add(
            Warn(guild_id=GUILD, user_id=9, moderator_id=1, reason="спам", created_at=now)
        )
        await uow.commit()

    data = (await client.get(f"/api/guilds/{GUILD}/summary")).json()
    assert data == {"bans": 1, "warns_users": 1, "frozen": 1}


async def test_summary_requires_manage(container):
    app = create_app(container)
    token = _cookie(container.settings, {999})
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={SESSION_COOKIE: token}
    ) as c:
        resp = await c.get(f"/api/guilds/{GUILD}/summary")
    assert resp.status_code == 403


# --- активность: сообщения/день + хитмап ------------------------------------


async def test_activity_daily_and_heatmap(client, uow_factory):
    """Почасовые счётчики -> суммы по дням + сетка день-недели×час; повторная
    доливка той же корзины инкрементит (upsert count += delta)."""
    from datetime import UTC, datetime, timedelta

    today = datetime.now(UTC).date()
    yday = today - timedelta(days=1)
    async with uow_factory() as uow:
        await uow.message_activity.add(GUILD, {(today, 10): 3, (today, 11): 2, (yday, 10): 5})
        await uow.message_activity.add(GUILD, {(today, 10): 1})  # инкремент той же корзины
        await uow.commit()

    data = (await client.get(f"/api/guilds/{GUILD}/activity?days=30")).json()
    # сообщения/день: старые -> новые; today = 4 (3+1) + 2 = 6
    assert data["daily"][0] == [yday.isoformat(), 5]
    assert data["daily"][-1] == [today.isoformat(), 6]
    # хитмап 7×24, значения по (weekday, час в UTC)
    hm = data["heatmap"]
    assert len(hm) == 7 and all(len(row) == 24 for row in hm)
    assert hm[today.weekday()][10] == 4
    assert hm[today.weekday()][11] == 2
    assert hm[yday.weekday()][10] == 5


async def test_activity_empty_when_no_messages(client):
    data = (await client.get(f"/api/guilds/{GUILD}/activity")).json()
    assert data["daily"] == []
    assert data["heatmap"] == [[0] * 24 for _ in range(7)]


async def test_activity_requires_manage(container):
    app = create_app(container)
    token = _cookie(container.settings, {999})
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={SESSION_COOKIE: token}
    ) as c:
        resp = await c.get(f"/api/guilds/{GUILD}/activity")
    assert resp.status_code == 403


# --- write: убрать фильм из вотчлиста / удалить плейлист ---------------------


async def test_cinema_remove_from_watchlist(client, uow_factory):
    from datetime import UTC, datetime

    from src.domain.cinema.entities import MovieEntry

    now = datetime.now(UTC)
    async with uow_factory() as uow:
        entry = await uow.movies.add(
            MovieEntry(guild_id=GUILD, title="Дюна", added_by=1, added_at=now, status="listed")
        )
        await uow.commit()

    resp = await client.delete(f"/api/guilds/{GUILD}/cinema/movies/{entry.id}")
    assert resp.status_code == 200 and resp.json()["status"] == "ok"
    data = (await client.get(f"/api/guilds/{GUILD}/cinema")).json()
    assert all(m["id"] != entry.id for m in data["watchlist"])
    # повторное удаление -> 404
    assert (await client.delete(f"/api/guilds/{GUILD}/cinema/movies/{entry.id}")).status_code == 404


async def test_music_delete_playlist(client, uow_factory):
    from src.domain.music.entities import Playlist, Track

    async with uow_factory() as uow:
        await uow.playlists.save(
            Playlist(
                guild_id=GUILD,
                name="todelete",
                created_by=3,  # не совпадает с сессией (user 1), но админ панели удаляет
                tracks=[Track(video_id="a", title="T", url="u", duration=10, requested_by=3)],
            )
        )
        await uow.commit()

    resp = await client.delete(f"/api/guilds/{GUILD}/music/playlists/todelete")
    assert resp.status_code == 200 and resp.json()["status"] == "ok"
    assert (await client.get(f"/api/guilds/{GUILD}/music/playlists/todelete")).status_code == 404
    # повторное удаление -> 404
    assert (await client.delete(f"/api/guilds/{GUILD}/music/playlists/todelete")).status_code == 404


# --- журнал действий панели -------------------------------------------------


async def test_audit_records_panel_action(client, monkeypatch):
    from src.api.routers import guilds as guilds_router
    from src.api.routers import people as people_router

    async def fake_users(_token, ids):
        return {uid: {"username": f"user{uid}", "avatar": None} for uid in ids}

    monkeypatch.setattr(guilds_router, "fetch_users", fake_users)
    monkeypatch.setattr(people_router, "fetch_users", fake_users)

    # действие через панель: правка очков user 5 (сессия = user 1)
    resp = await client.put(f"/api/guilds/{GUILD}/people/5/points", json={"value": 42})
    assert resp.status_code == 200

    audit = (await client.get(f"/api/guilds/{GUILD}/audit")).json()
    assert len(audit) >= 1
    top = audit[0]  # новые -> старые
    assert top["action"] == "points.set"
    assert top["actor_id"] == "1" and top["actor_name"] == "user1"
    assert top["target"] == "5" and top["target_name"] == "user5"
    assert "value" in (top["details"] or "")


async def test_audit_requires_manage(container):
    app = create_app(container)
    token = _cookie(container.settings, {999})  # не управляет сервером 10
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={SESSION_COOKIE: token}
    ) as c:
        resp = await c.get(f"/api/guilds/{GUILD}/audit")
    assert resp.status_code == 403


# --- музыка live: сейчас играет ---------------------------------------------


async def test_music_now_playing(client, uow_factory, monkeypatch):
    from datetime import UTC, datetime

    from src.api.routers import music as music_router
    from src.domain.player.entities import PlayerState, PlayerTrack

    async def fake_users(_token, ids):
        return {uid: {"username": f"u{uid}", "avatar": None} for uid in ids}

    monkeypatch.setattr(music_router, "fetch_users", fake_users)

    now = datetime.now(UTC)
    async with uow_factory() as uow:
        await uow.player_state.save(
            PlayerState(
                guild_id=GUILD,
                is_active=True,
                current=PlayerTrack(
                    title="Song",
                    url="http://x",
                    duration=200,
                    requested_by=5,
                    uploader="Artist",
                    thumbnail=None,
                ),
                queue=[PlayerTrack(title="Next", url="http://y", duration=100, requested_by=6)],
                position_seconds=42,
                is_paused=False,
                repeat="all",
                volume=0.8,
                position_at=now,
                updated_at=now,
            )
        )
        await uow.commit()

    data = (await client.get(f"/api/guilds/{GUILD}/music/now")).json()
    assert data["current"]["title"] == "Song" and data["current"]["requested_name"] == "u5"
    assert data["position_seconds"] == 42 and data["repeat"] == "all"
    assert [t["title"] for t in data["queue"]] == ["Next"]


async def test_music_now_null_when_inactive(client, uow_factory):
    from datetime import UTC, datetime

    from src.domain.player.entities import PlayerState

    async with uow_factory() as uow:
        await uow.player_state.save(
            PlayerState(guild_id=GUILD, is_active=False, updated_at=datetime.now(UTC))
        )
        await uow.commit()

    resp = await client.get(f"/api/guilds/{GUILD}/music/now")
    assert resp.status_code == 200 and resp.json() is None


# --- модули (вкл/выкл фич на сервере) ---------------------------------------


async def test_modules_list_and_toggle(client):
    mods = (await client.get(f"/api/guilds/{GUILD}/settings/modules")).json()
    activity = next(m for m in mods if m["key"] == "activity")
    assert activity["description"]  # на карточке есть описание модуля
    assert activity["master"]["key"] == "activity_enabled"
    assert activity["master"]["value"] is True and activity["master"]["is_override"] is False
    assert any(s["key"] == "activity_album" for s in activity["subs"])

    # выключаем подфункцию через обычный PUT настройки
    r = await client.put(f"/api/guilds/{GUILD}/settings/activity_album", json={"value": False})
    assert r.status_code == 200 and r.json()["value"] is False

    mods2 = (await client.get(f"/api/guilds/{GUILD}/settings/modules")).json()
    album = next(
        s
        for m in mods2
        if m["key"] == "activity"
        for s in m["subs"]
        if s["key"] == "activity_album"
    )
    assert album["value"] is False and album["is_override"] is True


async def test_settings_list_excludes_module_flags(client):
    fields = {f["key"] for f in (await client.get(f"/api/guilds/{GUILD}/settings")).json()}
    assert "warn_threshold" in fields  # обычные настройки на месте
    assert "activity_album" not in fields  # тумблеры модулей — на вкладке «Модули»
    assert "activity_enabled" not in fields
    assert "achievements_enabled" not in fields  # ачивки — тоже модуль, не «Настройки»


async def test_achievements_module_exposed_and_toggleable(client):
    # модуль «Достижения» доступен во вкладке «Модули» и выключается через PUT
    mods = (await client.get(f"/api/guilds/{GUILD}/settings/modules")).json()
    ach = next(m for m in mods if m["key"] == "achievements")
    assert ach["master"]["key"] == "achievements_enabled"
    assert ach["master"]["value"] is True and ach["description"]

    r = await client.put(
        f"/api/guilds/{GUILD}/settings/achievements_enabled", json={"value": False}
    )
    assert r.status_code == 200 and r.json()["value"] is False

    mods2 = (await client.get(f"/api/guilds/{GUILD}/settings/modules")).json()
    ach2 = next(m for m in mods2 if m["key"] == "achievements")
    assert ach2["master"]["value"] is False and ach2["master"]["is_override"] is True


# --- профиль бота на сервере ------------------------------------------------


async def test_bot_profile_get_default(client):
    p = (await client.get(f"/api/guilds/{GUILD}/bot-profile")).json()
    assert p == {
        "nick": "",
        "avatar_url": "",
        "banner_url": "",
        "avatar_data": "",
        "banner_data": "",
    }


async def test_bot_profile_upload_avatar(client, container):
    import base64

    data_url = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0jpegbytes").decode()

    async def execute(_cmd):
        return "Профиль обновлён: avatar."

    task, stop, seen = await _fake_bot(container, execute)
    try:
        resp = await client.put(
            f"/api/guilds/{GUILD}/bot-profile",
            json={"nick": "", "avatar_url": "", "banner_url": "", "avatar_data": data_url},
        )
    finally:
        stop.set()
        await task
    assert resp.status_code == 200 and resp.json()["command"]["status"] == "done"
    # загруженный аватар ушёл в мост и сохранён (кэш) — GET отдаёт целиком
    assert seen[0].payload["avatar_data"] == data_url
    assert (await client.get(f"/api/guilds/{GUILD}/bot-profile")).json()["avatar_data"] == data_url


async def test_bot_profile_upload_banner(client, container):
    import base64

    data_url = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0bannerbytes").decode()

    async def execute(_cmd):
        return "Профиль обновлён: banner."

    task, stop, seen = await _fake_bot(container, execute)
    try:
        resp = await client.put(
            f"/api/guilds/{GUILD}/bot-profile",
            json={"nick": "", "avatar_url": "", "banner_url": "", "banner_data": data_url},
        )
    finally:
        stop.set()
        await task
    assert resp.status_code == 200 and resp.json()["command"]["status"] == "done"
    # загруженный баннер ушёл в мост и сохранён (кэш) — GET отдаёт целиком
    assert seen[0].payload["banner_data"] == data_url
    assert (await client.get(f"/api/guilds/{GUILD}/bot-profile")).json()["banner_data"] == data_url


async def test_bot_profile_save_and_apply(client, container):
    async def execute(_cmd):
        return "Профиль обновлён: nick."

    task, stop, seen = await _fake_bot(container, execute)
    try:
        resp = await client.put(
            f"/api/guilds/{GUILD}/bot-profile",
            json={"nick": "Попося-тест", "avatar_url": "", "banner_url": ""},
        )
    finally:
        stop.set()
        await task
    assert resp.status_code == 200
    data = resp.json()
    assert data["command"]["status"] == "done"
    assert seen[0].command_type == "profile.apply"
    assert seen[0].payload["nick"] == "Попося-тест"
    # сохранилось — GET возвращает
    p2 = (await client.get(f"/api/guilds/{GUILD}/bot-profile")).json()
    assert p2["nick"] == "Попося-тест"
