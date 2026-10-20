from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.riot.entities import TrackedRiotGame
from src.domain.riot.repository import ITrackedRiotGameRepository
from src.infrastructure.db.dml import rows_affected
from src.infrastructure.db.models.riot import RiotTrackedGameModel


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _naive(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=None) if value is not None else None


def _to_domain(row: RiotTrackedGameModel) -> TrackedRiotGame:
    return TrackedRiotGame(
        id=row.id,
        guild_id=row.guild_id,
        game=row.game,
        name=row.name,
        thread_id=row.thread_id,
        last_news_gid=row.last_news_gid,
        last_news_date=_aware(row.last_news_date),
        added_by=row.added_by,
        created_at=_aware(row.created_at),
    )


class SqlAlchemyTrackedRiotGameRepository(ITrackedRiotGameRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, game: TrackedRiotGame) -> TrackedRiotGame:
        row = RiotTrackedGameModel(
            guild_id=game.guild_id,
            game=game.game,
            name=game.name,
            thread_id=game.thread_id,
            last_news_gid=game.last_news_gid,
            last_news_date=_naive(game.last_news_date),
            added_by=game.added_by,
            created_at=_naive(game.created_at) or datetime.now(UTC).replace(tzinfo=None),
        )
        self._session.add(row)
        await self._session.flush()  # получить id
        game.id = row.id
        return game

    async def get(self, guild_id: int, game: str) -> TrackedRiotGame | None:
        stmt = (
            select(RiotTrackedGameModel)
            .where(RiotTrackedGameModel.guild_id == guild_id, RiotTrackedGameModel.game == game)
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(row) if row else None

    async def list_for_guild(self, guild_id: int) -> list[TrackedRiotGame]:
        stmt = (
            select(RiotTrackedGameModel)
            .where(RiotTrackedGameModel.guild_id == guild_id)
            .order_by(RiotTrackedGameModel.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(row) for row in rows]

    async def list_all(self) -> list[TrackedRiotGame]:
        stmt = select(RiotTrackedGameModel).order_by(RiotTrackedGameModel.id)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(row) for row in rows]

    async def count_all(self) -> int:
        stmt = select(func.count()).select_from(RiotTrackedGameModel)
        return int((await self._session.execute(stmt)).scalar_one())

    async def remove(self, guild_id: int, game: str) -> bool:
        result = await self._session.execute(
            delete(RiotTrackedGameModel).where(
                RiotTrackedGameModel.guild_id == guild_id,
                RiotTrackedGameModel.game == game,
            )
        )
        return rows_affected(result) > 0

    async def mark_announced(self, game_id: int, gid: str, date: datetime) -> None:
        row = await self._session.get(RiotTrackedGameModel, game_id)
        if row is None:
            return
        row.last_news_gid = gid
        row.last_news_date = _naive(date)
