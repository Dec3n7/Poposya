import asyncio
from datetime import datetime, timezone

import pytest

from src.application.interfaces.audio_source import IAudioSource
from src.application.interfaces.unit_of_work import IUnitOfWork
from src.application.music.use_cases import (
    ListLikedUseCase,
    RemoveLikedUseCase,
    ResolveLikedUseCase,
    ToggleLikeUseCase,
)
from src.domain.music.entities import LikedTrack, Track
from src.domain.music.exceptions import TrackResolveError
from src.domain.music.repository import ILikedTrackRepository

NOW = datetime(2026, 7, 10, 22, 0, tzinfo=timezone.utc)


def make_track(video_id: str, title: str = "Song") -> Track:
    return Track(
        video_id=video_id,
        title=title,
        url=f"https://www.youtube.com/watch?v={video_id}",
        duration=180,
        requested_by=1,
        uploader="Artist",
    )


class FakeLiked(ILikedTrackRepository):
    def __init__(self):
        self.rows: dict[int, LikedTrack] = {}
        self._seq = 0

    async def get(self, user_id, video_id):
        return next(
            (r for r in self.rows.values() if r.user_id == user_id and r.video_id == video_id),
            None,
        )

    async def add(self, liked):
        self._seq += 1
        self.rows[self._seq] = LikedTrack(
            id=self._seq,
            user_id=liked.user_id,
            video_id=liked.video_id,
            title=liked.title,
            uploader=liked.uploader,
            duration=liked.duration,
            liked_at=liked.liked_at,
        )

    async def remove(self, user_id, video_id):
        for key, row in list(self.rows.items()):
            if row.user_id == user_id and row.video_id == video_id:
                del self.rows[key]
                return True
        return False

    async def list_for_user(self, user_id):
        rows = [r for r in self.rows.values() if r.user_id == user_id]
        return sorted(rows, key=lambda r: r.liked_at, reverse=True)

    async def count(self, user_id):
        return len([r for r in self.rows.values() if r.user_id == user_id])

    async def update_resolution(self, liked_id, video_id, title, uploader, duration):
        old = self.rows[liked_id]
        self.rows[liked_id] = LikedTrack(
            id=liked_id,
            user_id=old.user_id,
            video_id=video_id,
            title=title,
            uploader=uploader,
            duration=duration,
            liked_at=old.liked_at,
        )


class FakeUoW(IUnitOfWork):
    def __init__(self, liked: FakeLiked):
        self.liked_tracks = liked

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    def add_event(self, event):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass


class DeadVideoAudio(IAudioSource):
    """resolve всегда падает (видео умерло), search находит замену."""

    def __init__(self):
        self.searched: list[str] = []

    async def search(self, query, requested_by, limit=5):
        self.searched.append(query)
        return [make_track("fresh123", "Song (Re-upload)")]

    async def resolve(self, url, requested_by, playlist_limit=50):
        raise TrackResolveError("video unavailable")

    async def get_stream_url(self, track):
        return f"stream:{track.video_id}"


class AliveVideoAudio(IAudioSource):
    async def search(self, query, requested_by, limit=5):
        raise AssertionError("живое видео не должно искаться заново")

    async def resolve(self, url, requested_by, playlist_limit=50):
        video_id = url.rsplit("v=", 1)[-1]
        return [make_track(video_id)]

    async def get_stream_url(self, track):
        return f"stream:{track.video_id}"


@pytest.fixture
def liked_repo():
    return FakeLiked()


@pytest.fixture
def uow_factory(liked_repo):
    return lambda: FakeUoW(liked_repo)


async def test_toggle_like_and_unlike(uow_factory, liked_repo):
    uc = ToggleLikeUseCase(uow_factory, max_per_user=300)
    assert await uc.execute(1, make_track("abc"), NOW) == "liked"
    assert await liked_repo.count(1) == 1
    assert await uc.execute(1, make_track("abc"), NOW) == "unliked"
    assert await liked_repo.count(1) == 0


async def test_like_limit(uow_factory):
    uc = ToggleLikeUseCase(uow_factory, max_per_user=2)
    await uc.execute(1, make_track("a"), NOW)
    await uc.execute(1, make_track("b"), NOW)
    assert await uc.execute(1, make_track("c"), NOW) == "limit"
    # у другого пользователя свой счётчик
    assert await uc.execute(2, make_track("c"), NOW) == "liked"


async def test_lists_are_personal(uow_factory):
    toggle = ToggleLikeUseCase(uow_factory, max_per_user=300)
    listing = ListLikedUseCase(uow_factory)
    await toggle.execute(1, make_track("a"), NOW)
    await toggle.execute(2, make_track("b"), NOW)
    assert [t.video_id for t in await listing.execute(1)] == ["a"]
    assert [t.video_id for t in await listing.execute(2)] == ["b"]


async def test_remove_liked(uow_factory):
    toggle = ToggleLikeUseCase(uow_factory, max_per_user=300)
    remove = RemoveLikedUseCase(uow_factory)
    await toggle.execute(1, make_track("a"), NOW)
    assert await remove.execute(1, "a")
    assert not await remove.execute(1, "a")


async def test_resolve_alive_video_uses_video_id(uow_factory):
    toggle = ToggleLikeUseCase(uow_factory, max_per_user=300)
    await toggle.execute(1, make_track("abc"), NOW)
    uc = ResolveLikedUseCase(uow_factory, AliveVideoAudio())
    track = await uc.execute(1, "abc", requested_by=1)
    assert track is not None and track.video_id == "abc"


async def test_resolve_dead_video_heals_by_search(uow_factory, liked_repo):
    toggle = ToggleLikeUseCase(uow_factory, max_per_user=300)
    await toggle.execute(1, make_track("dead000", "Old Song"), NOW)
    audio = DeadVideoAudio()
    uc = ResolveLikedUseCase(uow_factory, audio)
    track = await uc.execute(1, "dead000", requested_by=1)
    assert track is not None and track.video_id == "fresh123"
    assert audio.searched == ["Old Song Artist"]
    # запись в лайках самовылечилась
    assert await liked_repo.get(1, "dead000") is None
    healed = await liked_repo.get(1, "fresh123")
    assert healed is not None and healed.title == "Song (Re-upload)"


async def test_resolve_unknown_video_returns_none(uow_factory):
    uc = ResolveLikedUseCase(uow_factory, AliveVideoAudio())
    assert await uc.execute(1, "nope", requested_by=1) is None
