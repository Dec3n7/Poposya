"""Прямые тесты SQL-репозитория tracked_repos на SQLite: добавление,
уникальность, поиск/список/счётчик, удаление, сдвиг отметки и etag."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from src.domain.repos.entities import TrackedRepo
from src.infrastructure.db.repositories.repos import SqlAlchemyTrackedRepoRepository

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def make_repo(**over):
    base = dict(
        guild_id=10, owner="psf", name="requests", thread_id=555, added_by=1, created_at=NOW
    )
    base.update(over)
    return TrackedRepo(**base)


async def test_add_assigns_id_and_roundtrips(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyTrackedRepoRepository(s)
        saved = await repo.add(make_repo(last_release_id=7, last_published_at=NOW))
        await s.commit()
        assert saved.id is not None
        loaded = await repo.get(10, "psf", "requests")
        assert loaded is not None
        assert loaded.full_name == "psf/requests"
        assert loaded.thread_id == 555
        assert loaded.last_release_id == 7
        assert loaded.last_published_at == NOW


async def test_unique_per_guild_owner_name(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyTrackedRepoRepository(s)
        await repo.add(make_repo())
        await s.commit()
    async with session_factory() as s:
        repo = SqlAlchemyTrackedRepoRepository(s)
        # add() делает flush() ради id — нарушение уникальности всплывает сразу
        with pytest.raises(IntegrityError):
            await repo.add(make_repo())


async def test_list_and_count(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyTrackedRepoRepository(s)
        await repo.add(make_repo(name="requests"))
        await repo.add(make_repo(name="black"))
        await repo.add(make_repo(guild_id=20, name="flask", owner="pallets"))
        await s.commit()
        guild10 = await repo.list_for_guild(10)
        assert {r.name for r in guild10} == {"requests", "black"}
        assert await repo.count_all() == 3
        assert len(await repo.list_all()) == 3


async def test_remove(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyTrackedRepoRepository(s)
        await repo.add(make_repo())
        await s.commit()
        assert await repo.remove(10, "psf", "requests") is True
        await s.commit()
        assert await repo.get(10, "psf", "requests") is None
        assert await repo.remove(10, "psf", "requests") is False


async def test_mark_announced_advances_marker_and_etag(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyTrackedRepoRepository(s)
        saved = await repo.add(make_repo())
        await s.commit()
        later = NOW + timedelta(hours=1)
        await repo.mark_announced(saved.id, 99, later, "etag-xyz")
        await s.commit()
        loaded = await repo.get(10, "psf", "requests")
        assert loaded.last_release_id == 99
        assert loaded.last_published_at == later
        assert loaded.etag == "etag-xyz"


async def test_mark_announced_keeps_etag_when_none(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyTrackedRepoRepository(s)
        saved = await repo.add(make_repo(etag="orig"))
        await s.commit()
        await repo.mark_announced(saved.id, 5, NOW, None)
        await s.commit()
        loaded = await repo.get(10, "psf", "requests")
        assert loaded.last_release_id == 5
        assert loaded.etag == "orig"


async def test_save_etag(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyTrackedRepoRepository(s)
        saved = await repo.add(make_repo(etag="old"))
        await s.commit()
        await repo.save_etag(saved.id, "new")
        await s.commit()
        loaded = await repo.get(10, "psf", "requests")
        assert loaded.etag == "new"
