from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from src.application.interfaces.unit_of_work import IUnitOfWork

UowFactory = Callable[[], IUnitOfWork]


@dataclass(frozen=True)
class ActivityStats:
    """Витрина активности для «Обзора»: сообщения по дням + две сетки день-недели×час.

    heatmap — сумма сообщений в ячейке за период; voice_heatmap — минуты присутствия
    в войсе (человеко-минуты). Обе 7 строк (Пн=0 … Вс=6) × 24 столбца (час UTC).
    daily — [(день, сумма сообщений)] старые→новые."""

    daily: list[tuple[date, int]]
    heatmap: list[list[int]]
    voice_heatmap: list[list[int]]


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


class RecordVoiceActivityUseCase:
    """Доливает почасовые человеко-секунды присутствия в войсе (бот копит в памяти
    и сбрасывает пачкой). Инкремент идемпотентен к повтору корзины."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, buckets: dict[tuple[date, int], int]) -> None:
        if not buckets:
            return
        async with self._uow_factory() as uow:
            await uow.message_activity.add_voice(guild_id, buckets)
            await uow.commit()


def _to_heatmap(rows: list[tuple[date, int, int]]) -> list[list[int]]:
    """Строки (день, час, значение) -> сетка 7×24 (weekday×час), суммируя ячейки.
    Weekday из даты — портируемо между SQLite и Postgres."""
    heatmap = [[0] * 24 for _ in range(7)]
    for day, hour, value in rows:
        if 0 <= hour < 24:
            heatmap[day.weekday()][hour] += value
    return heatmap


class GetActivityStatsUseCase:
    """Активность сервера за период: сообщения/день + два хитмапа день-недели×час
    (сообщения и минуты в войсе)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, since: date) -> ActivityStats:
        async with self._uow_factory() as uow:
            daily = await uow.message_activity.daily(guild_id, since)
            msg_rows = await uow.message_activity.hourly(guild_id, since)
            voice_rows = await uow.message_activity.voice_hourly(guild_id, since)
        # войс копится в секундах — на витрину отдаём минуты (человеко-минуты)
        voice_minutes = [(day, hour, seconds // 60) for day, hour, seconds in voice_rows]
        return ActivityStats(
            daily=daily,
            heatmap=_to_heatmap(msg_rows),
            voice_heatmap=_to_heatmap(voice_minutes),
        )
