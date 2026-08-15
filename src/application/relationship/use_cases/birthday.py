"""Дни рождения: установка своей даты, ближайшие ДР сервера (виджет «Обзора»)
и периодический тик - кого напомнить заранее и кого поздравить сегодня."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ._common import UowFactory


class SetBirthdayUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int, day: int, month: int) -> bool:
        """False - такой даты не существует."""
        try:
            date(2000, month, day)  # 2000 - високосный: 29 февраля допустимо
        except ValueError:
            return False
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get_or_create(user_id, guild_id)
            profile.birthday_day = day
            profile.birthday_month = month
            await uow.relationships.save(profile)
            await uow.commit()
            return True


def _days_until_birthday(today: date, month: int, day: int) -> int:
    """Дней до ближайшего наступления даты (в этом году или следующем).
    29 февраля в невисокосный год клэмпится к 28-му."""
    import calendar

    for year in (today.year, today.year + 1):
        d = min(day, calendar.monthrange(year, month)[1])
        bd = date(year, month, d)
        if bd >= today:
            return (bd - today).days
    return 0


class UpcomingBirthdaysUseCase:
    """Ближайшие дни рождения сервера: (user_id, месяц, день, дней_до),
    по возрастанию. Виджет на «Обзоре»."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self, guild_id: int, today: date, limit: int = 5
    ) -> list[tuple[int, int, int, int]]:
        async with self._uow_factory() as uow:
            profiles = await uow.relationships.all_for_guild(guild_id)
        upcoming = [
            (
                p.user_id,
                p.birthday_month,
                p.birthday_day,
                _days_until_birthday(today, p.birthday_month, p.birthday_day),
            )
            for p in profiles
            if p.birthday_month and p.birthday_day
        ]
        upcoming.sort(key=lambda r: r[3])
        return upcoming[:limit]


@dataclass(frozen=True)
class BirthdayEvents:
    remind: list[tuple[int, int]]  # (guild_id, user_id) - ДР через N дней
    congratulate: list[tuple[int, int]]  # (guild_id, user_id) - ДР сегодня


class BirthdayTickUseCase:
    """Периодическая проверка: кого напомнить (за remind_days) и кого
    поздравить сегодня. Дедупликация по году хранится в БД."""

    def __init__(self, uow_factory: UowFactory, remind_days: int):
        self._uow_factory = uow_factory
        self._remind_days = remind_days

    async def execute(self, now: datetime) -> BirthdayEvents:
        today = now.date()
        target = today + timedelta(days=self._remind_days)
        async with self._uow_factory() as uow:
            remind: list[tuple[int, int]] = []
            for profile in await uow.relationships.find_birthdays(target.month, target.day):
                marker = profile.birthday_reminded_at
                if marker is None or marker.year < today.year:
                    profile.birthday_reminded_at = today
                    await uow.relationships.save(profile)
                    remind.append((profile.guild_id, profile.user_id))

            congratulate: list[tuple[int, int]] = []
            for profile in await uow.relationships.find_birthdays(today.month, today.day):
                marker = profile.birthday_congratulated_at
                if marker is None or marker.year < today.year:
                    profile.birthday_congratulated_at = today
                    await uow.relationships.save(profile)
                    congratulate.append((profile.guild_id, profile.user_id))

            await uow.commit()
            return BirthdayEvents(remind=remind, congratulate=congratulate)
