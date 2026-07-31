"""Прямые тесты SQL-репозитория tracked_games на SQLite: добавление,
уникальность, поиск/список/счётчик, удаление, сдвиг отметки."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from src.domain.steam.entities import TrackedGame
from src.infrastructure.db.repositories.steam import SqlAlchemyTrackedGameRepository

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def make_game(**over):
    base = dict(guild_id=10, appid=730, name="CS2", thread_id=555, added_by=1, created_at=NOW)
    base.update(over)
    return TrackedGame(**base)


async def test_add_and_roundtrip(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyTrackedGameRepository(s)
        saved = await repo.add(make_game(last_news_gid="g1", last_news_date=NOW))
        await s.commit()
        assert saved.id is not None
        loaded = await repo.get(10, 730)
        assert loaded is not None
        assert loaded.name == "CS2"
        assert loaded.last_news_gid == "g1"
        assert loaded.last_news_date == NOW


async def test_unique_per_guild_appid(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyTrackedGameRepository(s)
        await repo.add(make_game())
        await s.commit()
    async with session_factory() as s:
        repo = SqlAlchemyTrackedGameRepository(s)
        with pytest.raises(IntegrityError):
            await repo.add(make_game())


async def test_list_count_remove(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyTrackedGameRepository(s)
        await repo.add(make_game(appid=730, name="CS2"))
        await repo.add(make_game(appid=440, name="TF2"))
        await repo.add(make_game(guild_id=20, appid=570, name="Dota"))
        await s.commit()
        assert {g.name for g in await repo.list_for_guild(10)} == {"CS2", "TF2"}
        assert await repo.count_all() == 3
        assert len(await repo.list_all()) == 3
        assert await repo.remove(10, 730) is True
        await s.commit()
        assert await repo.get(10, 730) is None
        assert await repo.remove(10, 730) is False


async def test_mark_announced(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyTrackedGameRepository(s)
        saved = await repo.add(make_game())
        await s.commit()
        later = NOW + timedelta(hours=3)
        await repo.mark_announced(saved.id, "gid-99", later)
        await s.commit()
        loaded = await repo.get(10, 730)
        assert loaded.last_news_gid == "gid-99"
        assert loaded.last_news_date == later
