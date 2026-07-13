"""Playlist use-cases поверх реального UoW+SQLite (покрывает и music-репозиторий)
плюс доменные свойства Track/LikedTrack."""

from datetime import UTC, datetime

from src.application.music.use_cases import (
    DeletePlaylistUseCase,
    ListPlaylistsUseCase,
    LoadPlaylistUseCase,
    SavePlaylistUseCase,
)
from src.domain.music.entities import LikedTrack, Track

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def make_track(vid, title=None):
    return Track(
        video_id=vid,
        title=title or vid,
        url=f"https://youtube.com/watch?v={vid}",
        duration=200,
        requested_by=1,
        uploader="Artist",
    )


# --- Track / LikedTrack domain ---------------------------------------------


def test_track_is_live():
    assert make_track("a").is_live is False
    live = Track("l", "Live", "u", None, 1)
    assert live.is_live is True


def test_liked_to_track_builds_url_and_thumbnail():
    liked = LikedTrack(
        user_id=1, video_id="abc", title="Song", uploader="Art", duration=100, liked_at=NOW
    )
    track = liked.to_track(requested_by=5)
    assert track.url == "https://www.youtube.com/watch?v=abc"
    assert track.requested_by == 5
    assert track.thumbnail == "https://i.ytimg.com/vi/abc/hqdefault.jpg"


# --- SavePlaylist -----------------------------------------------------------


async def test_save_empty_returns_empty_code(uow_factory):
    code = await SavePlaylistUseCase(uow_factory, max_per_guild=5, max_tracks=100).execute(
        10, "mix", 1, tracks=[]
    )
    assert code == "empty"


async def test_save_and_load_roundtrip(uow_factory):
    save = SavePlaylistUseCase(uow_factory, max_per_guild=5, max_tracks=100)
    assert await save.execute(10, "  Любимое  ", 1, [make_track("a"), make_track("b")]) == ""

    load = LoadPlaylistUseCase(uow_factory)
    tracks = await load.execute(10, "Любимое", requested_by=42)
    assert [t.video_id for t in tracks] == ["a", "b"]
    assert all(t.requested_by == 42 for t in tracks)  # играет от имени включившего


async def test_load_unknown_returns_none(uow_factory):
    assert await LoadPlaylistUseCase(uow_factory).execute(10, "нет", 1) is None


async def test_save_truncates_tracks(uow_factory):
    save = SavePlaylistUseCase(uow_factory, max_per_guild=5, max_tracks=2)
    await save.execute(10, "big", 1, [make_track("a"), make_track("b"), make_track("c")])
    tracks = await LoadPlaylistUseCase(uow_factory).execute(10, "big", 1)
    assert len(tracks) == 2


async def test_save_respects_guild_limit(uow_factory):
    save = SavePlaylistUseCase(uow_factory, max_per_guild=2, max_tracks=100)
    await save.execute(10, "one", 1, [make_track("a")])
    await save.execute(10, "two", 1, [make_track("b")])
    assert await save.execute(10, "three", 1, [make_track("c")]) == "limit"
    # перезапись существующего плейлиста лимит не задевает
    assert await save.execute(10, "one", 1, [make_track("z")]) == ""


async def test_list_playlists(uow_factory):
    save = SavePlaylistUseCase(uow_factory, max_per_guild=5, max_tracks=100)
    await save.execute(10, "a", 1, [make_track("x")])
    await save.execute(10, "b", 1, [make_track("y"), make_track("z")])
    names = await ListPlaylistsUseCase(uow_factory).execute(10)
    # кортеж репозитория: (name, count, created_by)
    by_name = {n: (count, creator) for n, count, creator in names}
    assert by_name["a"] == (1, 1)
    assert by_name["b"] == (2, 1)


# --- DeletePlaylist ---------------------------------------------------------


async def test_delete_by_author(uow_factory):
    save = SavePlaylistUseCase(uow_factory, max_per_guild=5, max_tracks=100)
    await save.execute(10, "mine", 7, [make_track("a")])
    delete = DeletePlaylistUseCase(uow_factory)
    assert await delete.execute(10, "mine", requester_id=7, is_admin=False) == "ok"
    assert await LoadPlaylistUseCase(uow_factory).execute(10, "mine", 1) is None


async def test_delete_forbidden_for_stranger(uow_factory):
    save = SavePlaylistUseCase(uow_factory, max_per_guild=5, max_tracks=100)
    await save.execute(10, "mine", 7, [make_track("a")])
    delete = DeletePlaylistUseCase(uow_factory)
    assert await delete.execute(10, "mine", requester_id=999, is_admin=False) == "forbidden"


async def test_delete_allowed_for_admin(uow_factory):
    save = SavePlaylistUseCase(uow_factory, max_per_guild=5, max_tracks=100)
    await save.execute(10, "mine", 7, [make_track("a")])
    delete = DeletePlaylistUseCase(uow_factory)
    assert await delete.execute(10, "mine", requester_id=999, is_admin=True) == "ok"


async def test_delete_not_found(uow_factory):
    delete = DeletePlaylistUseCase(uow_factory)
    assert await delete.execute(10, "ghost", requester_id=1, is_admin=True) == "not_found"
