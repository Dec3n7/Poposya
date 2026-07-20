from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.metrics.repository import IMetricsRepository
from src.infrastructure.db.models.metrics import GuildMetricDailyModel


def _upsert(session: AsyncSession):
    name = session.bind.dialect.name if session.bind is not None else "sqlite"
    return pg_insert if name == "postgresql" else sqlite_insert


class SqlAlchemyMetricsRepository(IMetricsRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def record(self, guild_id: int, day: date, values: dict[str, float]) -> None:
        # upsert по (guild_id, day, metric): повторный снапшот того же дня
        # перезаписывает значение — тик идемпотентен при рестартах/повторах
        insert = _upsert(self._session)
        for metric, value in values.items():
            stmt = insert(GuildMetricDailyModel).values(
                guild_id=guild_id, day=day, metric=metric, value=float(value)
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["guild_id", "day", "metric"],
                set_={"value": stmt.excluded.value},
            )
            await self._session.execute(stmt)

    async def series(
        self, guild_id: int, since: date
    ) -> dict[str, list[tuple[date, float]]]:
        stmt = (
            select(
                GuildMetricDailyModel.metric,
                GuildMetricDailyModel.day,
                GuildMetricDailyModel.value,
            )
            .where(
                GuildMetricDailyModel.guild_id == guild_id,
                GuildMetricDailyModel.day >= since,
            )
            .order_by(GuildMetricDailyModel.metric, GuildMetricDailyModel.day)
        )
        result: dict[str, list[tuple[date, float]]] = {}
        for metric, day, value in (await self._session.execute(stmt)).all():
            result.setdefault(metric, []).append((day, value))
        return result
