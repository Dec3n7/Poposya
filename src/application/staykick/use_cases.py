from collections.abc import Callable
from datetime import datetime, timedelta

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.staykick.entities import PendingKick

UowFactory = Callable[[], IUnitOfWork]


class SchedulePendingKickUseCase:
    """Ставит авто-кик участнику через hours часов с напоминанием за
    remind_before_minutes до срока. Возвращает момент кика."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self,
        guild_id: int,
        user_id: int,
        now: datetime,
        hours: int,
        remind_before_minutes: int,
    ) -> datetime:
        kick_at = now + timedelta(hours=hours)
        remind_at = kick_at - timedelta(minutes=remind_before_minutes)
        if remind_at <= now:
            remind_at = now  # окно короче напоминания — напомним сразу
        async with self._uow_factory() as uow:
            await uow.pending_kicks.schedule(
                PendingKick(
                    guild_id=guild_id,
                    user_id=user_id,
                    remind_at=remind_at,
                    kick_at=kick_at,
                    created_at=now,
                )
            )
            await uow.commit()
        return kick_at


class CancelPendingKickUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, user_id: int) -> bool:
        async with self._uow_factory() as uow:
            removed = await uow.pending_kicks.cancel(guild_id, user_id)
            await uow.commit()
            return removed


class PopDueKicksUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, now: datetime) -> list[PendingKick]:
        async with self._uow_factory() as uow:
            due = await uow.pending_kicks.pop_due_kicks(now)
            await uow.commit()
            return due


class DueRemindersUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, now: datetime) -> list[PendingKick]:
        async with self._uow_factory() as uow:
            due = await uow.pending_kicks.due_reminders(now)
            await uow.commit()
            return due
