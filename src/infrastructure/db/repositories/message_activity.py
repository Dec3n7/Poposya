from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.message_activity.repository import IMessageActivityRepository
from src.infrastructure.db.models.message_activity import MessageActivityModel


def _upsert(session: AsyncSession):
    name = session.bind.dialect.name if session.bind is not None else "sqlite"
    return pg_insert if name == "postgresql" else sqlite_insert


class SqlAlchemyMessageActivityRepository(IMessageActivityRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, guild_id: int, buckets: dict[tuple[date, int], int]) -> None:
        # инкремент count += delta по (guild_id, дата, час): доливка пачки из
        # бота идемпотентна к повторам корзины в рамках одного дня/часа
        insert = _upsert(self._session)
        for (bucket_date, bucket_hour), delta in buckets.items():
            if delta <= 0:
                continue
            stmt = insert(MessageActivityModel).values(
                guild_id=guild_id,
                bucket_date=bucket_date,
                bucket_hour=bucket_hour,
                count=int(delta),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["guild_id", "bucket_date", "bucket_hour"],
                set_={"count": MessageActivityModel.count + stmt.excluded.count},
            )
            await self._session.execute(stmt)

    async def daily(self, guild_id: int, since: date) -> list[tuple[date, int]]:
        stmt = (
            select(
                MessageActivityModel.bucket_date,
                func.sum(MessageActivityModel.count),
            )
            .where(
                MessageActivityModel.guild_id == guild_id,
                MessageActivityModel.bucket_date >= since,
            )
            .group_by(MessageActivityModel.bucket_date)
            .order_by(MessageActivityModel.bucket_date)
        )
        return [(day, int(total)) for day, total in (await self._session.execute(stmt)).all()]

    async def hourly(self, guild_id: int, since: date) -> list[tuple[date, int, int]]:
        stmt = (
            select(
                MessageActivityModel.bucket_date,
                MessageActivityModel.bucket_hour,
                MessageActivityModel.count,
            )
            .where(
                MessageActivityModel.guild_id == guild_id,
                MessageActivityModel.bucket_date >= since,
            )
            .order_by(MessageActivityModel.bucket_date, MessageActivityModel.bucket_hour)
        )
        return [
            (day, int(hour), int(count))
            for day, hour, count in (await self._session.execute(stmt)).all()
        ]
