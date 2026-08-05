from collections.abc import Callable
from datetime import date

from src.application.interfaces.unit_of_work import IUnitOfWork

UowFactory = Callable[[], IUnitOfWork]

# Канонические имена метрик снапшота — общий контракт бота (запись) и панели
# (чтение). Добавить метрику модуля = добавить ключ здесь и сборщик ниже; новая
# строка в узкой таблице, без миграции.
MEMBERS = "members"
POINTS_TOTAL = "points_total"
ACTIVE_PROFILES = "active_profiles"
WATCHLIST = "watchlist"
WATCHED = "watched"
PLAYLISTS = "playlists"
VOICE_HOURS = "voice_hours"
FINDS_COLLECTED = "finds_collected"


class RecordDailySnapshotUseCase:
    """Снимает числовые метрики сервера за UTC-день и идемпотентно пишет их в
    таблицу трендов. DB-метрики считает сам; те, что известны только боту
    (member_count из Discord), приходят в `extra`.

    Расширяемость: новая метрика = один сборщик в `values`. Все агрегаты — в
    одной транзакции, каждый одним запросом (без загрузки строк в память)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self, guild_id: int, day: date, extra: dict[str, float] | None = None
    ) -> dict[str, float]:
        async with self._uow_factory() as uow:
            points_total, active_profiles = await uow.relationships.points_summary(guild_id)
            values: dict[str, float] = {
                POINTS_TOTAL: points_total,
                ACTIVE_PROFILES: active_profiles,
                WATCHLIST: await uow.movies.count_listed(guild_id),
                WATCHED: await uow.movies.count_watched(guild_id),
                PLAYLISTS: await uow.playlists.count(guild_id),
                VOICE_HOURS: round(await uow.voice_progress.guild_total_minutes(guild_id) / 60, 2),
                FINDS_COLLECTED: await uow.collections.count_for_guild(guild_id),
            }
            if extra:
                values.update(extra)
            await uow.metrics.record(guild_id, day, values)
            await uow.commit()
            return values


class GetTrendsUseCase:
    """Серии метрик за последние `days` дней для панели: {metric: [(day, value)]}."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, since: date) -> dict[str, list[tuple[date, float]]]:
        async with self._uow_factory() as uow:
            return await uow.metrics.series(guild_id, since)
