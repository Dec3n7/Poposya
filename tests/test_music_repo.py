"""Прямые тесты SQL-репозитория лайкнутых треков на SQLite (фейки в
test_liked_tracks не задевают реальный репозиторий)."""

from datetime import datetime, timedelta, timezone

import pytest

from src.domain.music.entities import LikedTrack
from src.infrastructure.db.repositories.music import SqlAlchemyLikedTrackRepository

NOW = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)


def make_liked(video_id, title="Song", at=NOW):
    return LikedTrack(
        user_id=1,
        video_id=video_id,
        title=title,
        uploader="Artist",
        duration=200,
        liked_at=at,
    )


async def test_add_get_and_remove(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyLikedTrackRepository(s)
        await repo.add(make_liked("abc"))
        await s.commit()
        got = await repo.get(1, "abc")
        assert got is not None and got.title == "Song"
        assert got.liked_at == NOW  # tz восстановлен как UTC

        assert await repo.remove(1, "abc") is True
        await s.commit()
        assert await repo.get(1, "abc") is None
        assert await repo.remove(1, "abc") is False


async def test_list_ordered_desc_and_count(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyLikedTrackRepository(s)
        await repo.add(make_liked("old", "Old", NOW))
        await repo.add(make_liked("new", "New", NOW + timedelta(hours=1)))
        await s.commit()
        listing = await repo.list_for_user(1)
        assert [t.video_id for t in listing] == ["new", "old"]  # свежие раньше
        assert await repo.count(1) == 2
        assert await repo.count(2) == 0


async def test_update_resolution_heals_record(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyLikedTrackRepository(s)
        await repo.add(make_liked("dead", "Dead"))
        await s.commit()
        row = await repo.get(1, "dead")
        await repo.update_resolution(row.id, "fresh", "Fresh", "NewArtist", 250)
        await s.commit()
        assert await repo.get(1, "dead") is None
        healed = await repo.get(1, "fresh")
        assert healed.title == "Fresh" and healed.duration == 250


async def test_update_resolution_missing_id_noop(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyLikedTrackRepository(s)
        await repo.update_resolution(9999, "x", "X", None, None)  # не должно падать
        await s.commit()
