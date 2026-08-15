import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from src.application.interfaces.audio_source import IAudioSource
from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.music.entities import LikedTrack, Playlist, Track
from src.domain.music.exceptions import TrackResolveError

logger = logging.getLogger(__name__)

UowFactory = Callable[[], IUnitOfWork]


class SavePlaylistUseCase:
    def __init__(self, uow_factory: UowFactory, max_per_guild: int, max_tracks: int):
        self._uow_factory = uow_factory
        self._max_per_guild = max_per_guild
        self._max_tracks = max_tracks

    async def execute(self, guild_id: int, name: str, created_by: int, tracks: list[Track]) -> str:
        """Возвращает "" при успехе или код ошибки: empty | limit."""
        name = name.strip()[:50]
        if not tracks:
            return "empty"
        async with self._uow_factory() as uow:
            existing = await uow.playlists.get(guild_id, name)
            if existing is None and await uow.playlists.count(guild_id) >= self._max_per_guild:
                return "limit"
            await uow.playlists.save(
                Playlist(
                    guild_id=guild_id,
                    name=name,
                    created_by=created_by,
                    tracks=tracks[: self._max_tracks],
                )
            )
            await uow.commit()
            return ""


class LoadPlaylistUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, name: str, requested_by: int) -> list[Track] | None:
        async with self._uow_factory() as uow:
            playlist = await uow.playlists.get(guild_id, name.strip())
            if playlist is None:
                return None
            # треки играют от имени того, кто включил плейлист
            return [replace(track, requested_by=requested_by) for track in playlist.tracks]


class ListPlaylistsUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int) -> list[tuple[str, int, int]]:
        async with self._uow_factory() as uow:
            return await uow.playlists.list_names(guild_id)


class ToggleLikeUseCase:
    """Кнопка ❤️: лайк, если трека нет в списке, иначе — снятие лайка.
    Возвращает "liked" | "unliked" | "limit"."""

    def __init__(self, uow_factory: UowFactory, max_per_user: int):
        self._uow_factory = uow_factory
        self._max_per_user = max_per_user

    async def execute(
        self, user_id: int, track: Track, now: datetime, max_per_user: int | None = None
    ) -> str:
        # потолок можно переопределить на вызов (тарифный кламп по гильдии, где
        # нажали ❤️): free ≤20, Premium/Pro — конструкторный дефолт (300)
        cap = max_per_user if max_per_user is not None else self._max_per_user
        async with self._uow_factory() as uow:
            existing = await uow.liked_tracks.get(user_id, track.video_id)
            if existing is not None:
                await uow.liked_tracks.remove(user_id, track.video_id)
                await uow.commit()
                return "unliked"
            if await uow.liked_tracks.count(user_id) >= cap:
                return "limit"
            await uow.liked_tracks.add(
                LikedTrack(
                    user_id=user_id,
                    video_id=track.video_id,
                    title=track.title,
                    uploader=track.uploader,
                    duration=track.duration,
                    liked_at=now,
                )
            )
            await uow.commit()
            return "liked"


class ListLikedUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int) -> list[LikedTrack]:
        async with self._uow_factory() as uow:
            return await uow.liked_tracks.list_for_user(user_id)


class RemoveLikedUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, video_id: str) -> bool:
        async with self._uow_factory() as uow:
            removed = await uow.liked_tracks.remove(user_id, video_id)
            await uow.commit()
            return removed


class ResolveLikedUseCase:
    """Оживление лайкнутого трека перед воспроизведением: сначала по
    video_id; если видео умерло (удалено/заблокировано) — ищем по названию
    на YouTube и обновляем запись, чтобы список лечил себя сам."""

    def __init__(self, uow_factory: UowFactory, audio_source: IAudioSource):
        self._uow_factory = uow_factory
        self._audio = audio_source

    async def execute(self, user_id: int, video_id: str, requested_by: int) -> Track | None:
        async with self._uow_factory() as uow:
            liked = await uow.liked_tracks.get(user_id, video_id)
        if liked is None:
            return None

        try:
            tracks = await self._audio.resolve(
                liked.to_track(requested_by).url, requested_by=requested_by
            )
            if tracks:
                return tracks[0]
        except TrackResolveError:
            logger.info(
                "Лайкнутое видео умерло, ищу замену по названию",
                extra={"video_id": video_id, "title": liked.title},
            )

        query = f"{liked.title} {liked.uploader or ''}".strip()
        results = await self._audio.search(query, requested_by=requested_by, limit=1)
        if not results:
            return None
        fresh = results[0]
        async with self._uow_factory() as uow:
            row = await uow.liked_tracks.get(user_id, video_id)
            if row is not None and row.id is not None:
                duplicate = await uow.liked_tracks.get(user_id, fresh.video_id)
                if duplicate is not None:
                    # замена уже есть в лайках — старую мёртвую запись убираем
                    await uow.liked_tracks.remove(user_id, video_id)
                else:
                    await uow.liked_tracks.update_resolution(
                        row.id,
                        fresh.video_id,
                        fresh.title,
                        fresh.uploader,
                        fresh.duration,
                    )
                await uow.commit()
        return fresh


class DeletePlaylistUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, name: str, requester_id: int, is_admin: bool) -> str:
        """ok | not_found | forbidden — удалять может автор или админ."""
        async with self._uow_factory() as uow:
            playlist = await uow.playlists.get(guild_id, name.strip())
            if playlist is None:
                return "not_found"
            if playlist.created_by != requester_id and not is_admin:
                return "forbidden"
            await uow.playlists.delete(guild_id, playlist.name)
            await uow.commit()
            return "ok"
