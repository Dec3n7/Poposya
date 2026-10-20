"""Прямые тесты SQL-репозитория riot_tracked_games на SQLite: добавление,
уникальность пары (guild, game), поиск/список/счётчик, удаление, сдвиг отметки."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from src.domain.riot.entities import TrackedRiotGame
from src.infrastructure.db.repositories.riot import SqlAlchemyTrackedRiotGameRepository

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def make_game(**over):
    base = dict(
        guild_id=10,
        game="valorant",
        name="Valorant",
        thread_id=555,
        added_by=1,
        created_at=NOW,
    )
    base.update(over)
    return TrackedRiotGame(**base)


async def test_add_and_roundtrip(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyTrackedRiotGameRepository(s)
        saved = await repo.add(make_game(last_news_gid="g1", last_news_date=NOW))
        await s.commit()
        assert saved.id is not None
        loaded = await repo.get(10, "valorant")
        assert loaded is not None
        assert loaded.name == "Valorant"
        assert loaded.last_news_gid == "g1"
        assert loaded.last_news_date == NOW


async def test_unique_per_guild_game(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyTrackedRiotGameRepository(s)
        await repo.add(make_game())
        await s.commit()
    async with session_factory() as s:
        repo = SqlAlchemyTrackedRiotGameRepository(s)
        with pytest.raises(IntegrityError):
            await repo.add(make_game())


async def test_list_count_remove(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyTrackedRiotGameRepository(s)
        await repo.add(make_game(guild_id=10, game="valorant", name="Valorant"))
        await repo.add(make_game(guild_id=10, game="lol", name="LoL"))
        await repo.add(make_game(guild_id=20, game="valorant", name="Valorant"))
        await s.commit()
        assert {g.game for g in await repo.list_for_guild(10)} == {"valorant", "lol"}
        assert await repo.count_all() == 3
        assert len(await repo.list_all()) == 3
        assert await repo.remove(10, "valorant") is True
        await s.commit()
        assert await repo.get(10, "valorant") is None
        assert await repo.remove(10, "valorant") is False


async def test_mark_announced(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyTrackedRiotGameRepository(s)
        saved = await repo.add(make_game())
        await s.commit()
        later = NOW + timedelta(hours=3)
        await repo.mark_announced(saved.id, "gid-99", later)
        await s.commit()
        loaded = await repo.get(10, "valorant")
        assert loaded.last_news_gid == "gid-99"
        assert loaded.last_news_date == later
