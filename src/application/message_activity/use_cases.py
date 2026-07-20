from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from src.application.interfaces.unit_of_work import IUnitOfWork

UowFactory = Callable[[], IUnitOfWork]


@dataclass(frozen=True)
class ActivityStats:
    """Витрина активности для «Обзора»: сообщения по дням + сетка день-недели×час.

    heatmap — 7 строк (Пн=0 … Вс=6) × 24 столбца (час в UTC), значение = сумма
    сообщений в этой ячейке за период. daily — [(день, сумма)] старые→новые."""

    daily: list[tuple[date, int]]
    heatmap: list[list[int]]


class RecordMessageActivityUseCase:
    """Доливает почасовые счётчики сообщений (бот копит их в памяти и сбрасывает
    пачкой). Инкремент идемпотентен к повтору корзины."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, buckets: dict[tuple[date, int], int]) -> None:
        if not buckets:
            return
        async with self._uow_factory() as uow:
            await uow.message_activity.add(guild_id, buckets)
            await uow.commit()


class GetActivityStatsUseCase:
    """Активность сервера за период: сообщения/день + хитмап день-недели×час.
    Weekday считаем из даты (портируемо между SQLite и Postgres)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, since: date) -> ActivityStats:
        async with self._uow_factory() as uow:
            daily = await uow.message_activity.daily(guild_id, since)
            rows = await uow.message_activity.hourly(guild_id, since)
        heatmap = [[0] * 24 for _ in range(7)]
        for day, hour, count in rows:
            if 0 <= hour < 24:
                heatmap[day.weekday()][hour] += count
        return ActivityStats(daily=daily, heatmap=heatmap)
