import json
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.music.entities import LikedTrack, Playlist, Track
from src.domain.music.repository import ILikedTrackRepository, IPlaylistRepository
from src.infrastructure.db.models.music import GuildPlaylistModel, LikedTrackModel


def _upsert(session: AsyncSession):
    """Диалект-специфичный INSERT с ON CONFLICT (PG и SQLite 3.24+). Тот же
    приём, что в activity/relationship."""
    name = session.bind.dialect.name if session.bind is not None else "sqlite"
    return pg_insert if name == "postgresql" else sqlite_insert


def _tracks_to_json(tracks: list[Track]) -> str:
    return json.dumps(
        [
            {
                "video_id": t.video_id,
                "title": t.title,
                "url": t.url,
                "duration": t.duration,
                "uploader": t.uploader,
            }
            for t in tracks
        ],
        ensure_ascii=False,
    )


def _tracks_from_json(raw: str, created_by: int) -> list[Track]:
    return [
        Track(
            video_id=item.get("video_id", ""),
            title=item.get("title", "Без названия"),
            url=item.get("url", ""),
            duration=item.get("duration"),
            requested_by=created_by,
            uploader=item.get("uploader"),
            thumbnail=(
                f"https://i.ytimg.com/vi/{item['video_id']}/hqdefault.jpg"
                if item.get("video_id")
                else None
            ),
        )
        for item in json.loads(raw)
    ]


class SqlAlchemyPlaylistRepository(IPlaylistRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _get_row(self, guild_id: int, name: str) -> GuildPlaylistModel | None:
        stmt = select(GuildPlaylistModel).where(
            GuildPlaylistModel.guild_id == guild_id,
            GuildPlaylistModel.name == name,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get(self, guild_id: int, name: str) -> Playlist | None:
        row = await self._get_row(guild_id, name)
        if row is None:
            return None
        return Playlist(
            guild_id=row.guild_id,
            name=row.name,
            created_by=row.created_by,
            tracks=_tracks_from_json(row.tracks_json, row.created_by),
        )

    async def list_names(self, guild_id: int) -> list[tuple[str, int, int]]:
        stmt = (
            select(GuildPlaylistModel)
            .where(GuildPlaylistModel.guild_id == guild_id)
            .order_by(GuildPlaylistModel.name)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [(row.name, len(json.loads(row.tracks_json)), row.created_by) for row in rows]

    async def count(self, guild_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(GuildPlaylistModel)
            .where(GuildPlaylistModel.guild_id == guild_id)
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def save(self, playlist: Playlist) -> None:
        # INSERT ... ON CONFLICT DO UPDATE по (guild_id, name): плейлист всегда
        # пишется целиком (SavePlaylistUseCase не дельта, а полный список), так
        # что при гонке двух сохранений это осознанный last-write-wins, а не
        # порча. Без ON CONFLICT две параллельные вставки НОВОГО плейлиста одним
        # именем упирались бы в uq_playlist_guild_name с IntegrityError у одного.
        now = datetime.now(UTC).replace(tzinfo=None)
        tracks_json = _tracks_to_json(playlist.tracks)
        stmt = _upsert(self._session)(GuildPlaylistModel).values(
            guild_id=playlist.guild_id,
            name=playlist.name,
            created_by=playlist.created_by,
            tracks_json=tracks_json,
            created_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["guild_id", "name"],
            set_={"created_by": playlist.created_by, "tracks_json": tracks_json},
        )
        await self._session.execute(stmt)

    async def delete(self, guild_id: int, name: str) -> bool:
        result = await self._session.execute(
            delete(GuildPlaylistModel).where(
                GuildPlaylistModel.guild_id == guild_id,
                GuildPlaylistModel.name == name,
            )
        )
        return result.rowcount > 0


def _liked_to_domain(row: LikedTrackModel) -> LikedTrack:
    return LikedTrack(
        id=row.id,
        user_id=row.user_id,
        video_id=row.video_id,
        title=row.title,
        uploader=row.uploader,
        duration=row.duration,
        liked_at=row.liked_at.replace(tzinfo=UTC),
    )


class SqlAlchemyLikedTrackRepository(ILikedTrackRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, user_id: int, video_id: str) -> LikedTrack | None:
        stmt = select(LikedTrackModel).where(
            LikedTrackModel.user_id == user_id,
            LikedTrackModel.video_id == video_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _liked_to_domain(row) if row else None

    async def add(self, liked: LikedTrack) -> None:
        self._session.add(
            LikedTrackModel(
                user_id=liked.user_id,
                video_id=liked.video_id,
                title=liked.title,
                uploader=liked.uploader,
                duration=liked.duration,
                liked_at=liked.liked_at.replace(tzinfo=None),
            )
        )

    async def remove(self, user_id: int, video_id: str) -> bool:
        result = await self._session.execute(
            delete(LikedTrackModel).where(
                LikedTrackModel.user_id == user_id,
                LikedTrackModel.video_id == video_id,
            )
        )
        return result.rowcount > 0

    async def list_for_user(self, user_id: int) -> list[LikedTrack]:
        stmt = (
            select(LikedTrackModel)
            .where(LikedTrackModel.user_id == user_id)
            .order_by(LikedTrackModel.liked_at.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_liked_to_domain(row) for row in rows]

    async def count(self, user_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(LikedTrackModel)
            .where(LikedTrackModel.user_id == user_id)
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def update_resolution(
        self,
        liked_id: int,
        video_id: str,
        title: str,
        uploader: str | None,
        duration: int | None,
    ) -> None:
        row = await self._session.get(LikedTrackModel, liked_id)
        if row is None:
            return
        row.video_id = video_id
        row.title = title
        row.uploader = uploader
        row.duration = duration
