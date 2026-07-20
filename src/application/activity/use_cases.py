from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.activity.entities import Reminder

UowFactory = Callable[[], IUnitOfWork]


@dataclass(frozen=True)
class ActivityTouch:
    returned_after_absence: bool
    days_absent: int


class TouchMemberActivityUseCase:
    """Фиксирует сообщение участника; сообщает, вернулся ли он после
    долгого отсутствия (ABSENT_DAYS_THRESHOLD)."""

    def __init__(self, uow_factory: UowFactory, absent_days_threshold: int, settings_provider=None):
        self._uow_factory = uow_factory
        self._threshold = absent_days_threshold
        self._settings = settings_provider

    async def execute(self, user_id: int, guild_id: int, now: datetime) -> ActivityTouch:
        threshold = (
            self._settings.get(guild_id, "absent_days_threshold", self._threshold)
            if self._settings is not None
            else self._threshold
        )
        async with self._uow_factory() as uow:
            last = await uow.member_activity.get_last_message(user_id, guild_id)
            days_absent = (now - last).days if last is not None else 0
            returned = last is not None and days_absent >= threshold
            await uow.member_activity.set_last_message(user_id, guild_id, now)
            await uow.commit()
            return ActivityTouch(returned_after_absence=returned, days_absent=days_absent)


class AddReminderUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int, text: str, due_at: datetime) -> None:
        async with self._uow_factory() as uow:
            await uow.reminders.add(
                Reminder(user_id=user_id, guild_id=guild_id, text=text, due_at=due_at)
            )
            await uow.commit()


class PopDueRemindersUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, now: datetime) -> list[Reminder]:
        async with self._uow_factory() as uow:
            due = await uow.reminders.pop_due(now)
            await uow.commit()
            return due


class LoadVoiceProgressUseCase:
    """Восстановление счётчиков войс-минут после рестарта."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self) -> dict[tuple[int, int], float]:
        async with self._uow_factory() as uow:
            return await uow.voice_progress.load_all()


class SaveVoiceProgressUseCase:
    """Сохранение изменившихся за тик счётчиков войс-минут; заодно копится
    суммарное время для профиля."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self, progress: dict[tuple[int, int], float], accrued_minutes: float = 0.0
    ) -> None:
        if not progress:
            return
        async with self._uow_factory() as uow:
            await uow.voice_progress.save_many(progress, accrued_minutes)
            await uow.commit()


class GetVoiceHoursUseCase:
    """Суммарные часы в войсе — для профиля-витрины."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, user_id: int) -> float:
        async with self._uow_factory() as uow:
            return await uow.voice_progress.total_minutes(guild_id, user_id) / 60


class VoiceLeaderboardUseCase:
    """Топ по времени в войсе: (user_id, часы), убывание — для панели."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, limit: int = 10) -> list[tuple[int, float]]:
        async with self._uow_factory() as uow:
            rows = await uow.voice_progress.top_by_minutes(guild_id, limit)
        return [(uid, round(minutes / 60, 1)) for uid, minutes in rows]


class TryMarkAlbumPostUseCase:
    """True — сообщение ещё не было в альбоме и теперь помечено;
    False — уже публиковалось (дедупликация переживает рестарт)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, message_id: int, now: datetime) -> bool:
        async with self._uow_factory() as uow:
            marked = await uow.album_posts.try_mark(guild_id, message_id, now)
            await uow.commit()
            return marked
