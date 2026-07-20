import json
from datetime import UTC

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.player.entities import PlayerState, PlayerTrack
from src.domain.player.repository import IPlayerStateRepository
from src.infrastructure.db.models.player import PlayerStateModel


def _upsert(session: AsyncSession):
    name = session.bind.dialect.name if session.bind is not None else "sqlite"
    return pg_insert if name == "postgresql" else sqlite_insert


def _track_to_dict(t: PlayerTrack) -> dict:
    return {
        "title": t.title,
        "url": t.url,
        "duration": t.duration,
        "requested_by": t.requested_by,
        "uploader": t.uploader,
        "thumbnail": t.thumbnail,
    }


def _track_from_dict(d: dict) -> PlayerTrack:
    return PlayerTrack(
        title=d["title"],
        url=d["url"],
        duration=d.get("duration"),
        requested_by=d["requested_by"],
        uploader=d.get("uploader"),
        thumbnail=d.get("thumbnail"),
    )


class SqlAlchemyPlayerStateRepository(IPlayerStateRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(self, state: PlayerState) -> None:
        assert state.updated_at is not None
        values = {
            "guild_id": state.guild_id,
            "is_active": state.is_active,
            "current": json.dumps(_track_to_dict(state.current)) if state.current else None,
            "queue": json.dumps([_track_to_dict(t) for t in state.queue]),
            "position_seconds": state.position_seconds,
            "position_at": state.position_at.replace(tzinfo=None) if state.position_at else None,
            "is_paused": state.is_paused,
            "repeat": state.repeat,
            "volume": state.volume,
            "updated_at": state.updated_at.replace(tzinfo=None),
        }
        stmt = _upsert(self._session)(PlayerStateModel).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["guild_id"],
            set_={k: v for k, v in values.items() if k != "guild_id"},
        )
        await self._session.execute(stmt)

    async def get(self, guild_id: int) -> PlayerState | None:
        row = await self._session.get(PlayerStateModel, guild_id)
        if row is None:
            return None
        current = _track_from_dict(json.loads(row.current)) if row.current else None
        queue = [_track_from_dict(d) for d in json.loads(row.queue)]
        return PlayerState(
            guild_id=row.guild_id,
            is_active=row.is_active,
            current=current,
            queue=queue,
            position_seconds=row.position_seconds,
            is_paused=row.is_paused,
            repeat=row.repeat,
            volume=row.volume,
            position_at=row.position_at.replace(tzinfo=UTC) if row.position_at else None,
            updated_at=row.updated_at.replace(tzinfo=UTC),
        )
