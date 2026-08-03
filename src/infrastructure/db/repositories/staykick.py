from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.staykick.entities import PendingKick
from src.domain.staykick.repository import IPendingKickRepository
from src.infrastructure.db.dml import rows_affected
from src.infrastructure.db.models.staykick import PendingKickModel


def _to_domain(row: PendingKickModel) -> PendingKick:
    return PendingKick(
        guild_id=row.guild_id,
        user_id=row.user_id,
        remind_at=row.remind_at,
        kick_at=row.kick_at,
        reminded=row.reminded,
        created_at=row.created_at,
    )


class SqlAlchemyPendingKickRepository(IPendingKickRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def schedule(self, kick: PendingKick) -> None:
        # одна запись на (guild, user): заменяем прежнюю, если была
        await self._session.execute(
            delete(PendingKickModel).where(
                PendingKickModel.guild_id == kick.guild_id,
                PendingKickModel.user_id == kick.user_id,
            )
        )
        self._session.add(
            PendingKickModel(
                guild_id=kick.guild_id,
                user_id=kick.user_id,
                remind_at=kick.remind_at.replace(tzinfo=None),
                kick_at=kick.kick_at.replace(tzinfo=None),
                reminded=False,
                created_at=(kick.created_at or datetime.utcnow()).replace(tzinfo=None),
            )
        )

    async def cancel(self, guild_id: int, user_id: int) -> bool:
        result = await self._session.execute(
            delete(PendingKickModel).where(
                PendingKickModel.guild_id == guild_id,
                PendingKickModel.user_id == user_id,
            )
        )
        return rows_affected(result) > 0

    async def pop_due_kicks(self, now: datetime) -> list[PendingKick]:
        naive_now = now.replace(tzinfo=None)
        stmt = select(PendingKickModel).where(PendingKickModel.kick_at <= naive_now)
        rows = (await self._session.execute(stmt)).scalars().all()
        due = [_to_domain(row) for row in rows]
        if rows:
            await self._session.execute(
                delete(PendingKickModel).where(PendingKickModel.kick_at <= naive_now)
            )
        return due

    async def due_reminders(self, now: datetime) -> list[PendingKick]:
        naive_now = now.replace(tzinfo=None)
        stmt = select(PendingKickModel).where(
            PendingKickModel.reminded.is_(False),
            PendingKickModel.remind_at <= naive_now,
            PendingKickModel.kick_at > naive_now,
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        for row in rows:
            row.reminded = True  # помечаем — не напомним дважды
        return [_to_domain(row) for row in rows]
