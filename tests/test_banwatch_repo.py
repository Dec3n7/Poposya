"""Прямые тесты SQL-репозитория server_bans на SQLite: upsert, удаление,
список по пользователю, синхронизация сервера, отбор «отмеченных»."""

from datetime import UTC, datetime

from src.domain.banwatch.entities import ServerBan
from src.infrastructure.db.repositories.banwatch import SqlAlchemyServerBanRepository

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def ban(user_id=100, guild_id=1, **over):
    base = dict(user_id=user_id, guild_id=guild_id, guild_name=f"G{guild_id}", reason="spam")
    base.update(over)
    return ServerBan(**base)


async def test_upsert_and_list_for_user(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyServerBanRepository(s)
        await repo.upsert(ban(guild_id=1, banned_at=NOW))
        await repo.upsert(ban(guild_id=2, reason="raid"))
        await s.commit()
        rows = await repo.list_for_user(100)
        assert {r.guild_id for r in rows} == {1, 2}
        assert next(r for r in rows if r.guild_id == 1).banned_at == NOW


async def test_upsert_updates_existing(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyServerBanRepository(s)
        await repo.upsert(ban(guild_id=1, reason="old"))
        await repo.upsert(ban(guild_id=1, reason="new", guild_name="Renamed"))
        await s.commit()
        rows = await repo.list_for_user(100)
        assert len(rows) == 1
        assert rows[0].reason == "new"
        assert rows[0].guild_name == "Renamed"


async def test_remove(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyServerBanRepository(s)
        await repo.upsert(ban(guild_id=1))
        await repo.upsert(ban(guild_id=2))
        await s.commit()
        await repo.remove(1, 100)
        await s.commit()
        assert {r.guild_id for r in await repo.list_for_user(100)} == {2}


async def test_replace_guild(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyServerBanRepository(s)
        await repo.upsert(ban(user_id=100, guild_id=1))
        await repo.upsert(ban(user_id=200, guild_id=1))
        await s.commit()
        # бэкфилл: на сервере 1 теперь забанен только 300
        await repo.replace_guild(1, [ban(user_id=300, guild_id=1)])
        await s.commit()
        assert await repo.list_for_user(100) == []
        assert {r.user_id for r in await repo.list_for_user(300)} == {300}


async def test_flagged_candidates(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyServerBanRepository(s)
        # user 100 забанен на серверах 1,2,3,4; user 200 — только на 2,3
        for g in (1, 2, 3, 4):
            await repo.upsert(ban(user_id=100, guild_id=g))
        for g in (2, 3):
            await repo.upsert(ban(user_id=200, guild_id=g))
        await s.commit()

        # с точки зрения сервера 1: user 100 забанен на 2,3,4 = 3 → порог 3 пройден
        flagged = dict(await repo.flagged_candidates(exclude_guild_id=1, threshold=3))
        assert flagged == {100: 3}

        # порог 4 — уже никто (у 100 вне сервера 1 только 3)
        assert await repo.flagged_candidates(exclude_guild_id=1, threshold=4) == []

        # с точки зрения сервера 5 (где никого нет): 100→4, 200→2; порог 3 → только 100
        flagged5 = dict(await repo.flagged_candidates(exclude_guild_id=5, threshold=3))
        assert flagged5 == {100: 4}
