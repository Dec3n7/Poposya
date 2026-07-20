"""Снапшоты метрик (фундамент трендов на панели).

Проверяем реальный SqlAlchemy-стек (SQLite из conftest): узкую таблицу с
upsert-идемпотентностью, чтение серий и сборку метрик use-case'ом поверх тех же
репозиториев, что у бота.
"""

from datetime import date

from src.application.metrics.use_cases import (
    ACTIVE_PROFILES,
    MEMBERS,
    POINTS_TOTAL,
    WATCHLIST,
    GetTrendsUseCase,
    RecordDailySnapshotUseCase,
)
from src.domain.relationship.entities import RelationshipProfile

GUILD = 42


async def test_metrics_repo_series_ordered(uow_factory):
    async with uow_factory() as uow:
        await uow.metrics.record(GUILD, date(2026, 1, 1), {"members": 10})
        await uow.metrics.record(GUILD, date(2026, 1, 3), {"members": 12})
        await uow.metrics.record(GUILD, date(2026, 1, 2), {"members": 11})
        await uow.commit()

    async with uow_factory() as uow:
        series = await uow.metrics.series(GUILD, date(2026, 1, 1))

    assert series["members"] == [
        (date(2026, 1, 1), 10.0),
        (date(2026, 1, 2), 11.0),
        (date(2026, 1, 3), 12.0),
    ]


async def test_metrics_repo_upsert_same_day(uow_factory):
    async with uow_factory() as uow:
        await uow.metrics.record(GUILD, date(2026, 1, 1), {"members": 10})
        await uow.metrics.record(GUILD, date(2026, 1, 1), {"members": 99})  # перезапись
        await uow.commit()

    async with uow_factory() as uow:
        series = await uow.metrics.series(GUILD, date(2026, 1, 1))

    assert series["members"] == [(date(2026, 1, 1), 99.0)]


async def test_metrics_series_respects_since(uow_factory):
    async with uow_factory() as uow:
        await uow.metrics.record(GUILD, date(2026, 1, 1), {"members": 5})
        await uow.metrics.record(GUILD, date(2026, 1, 10), {"members": 7})
        await uow.commit()

    async with uow_factory() as uow:
        series = await uow.metrics.series(GUILD, date(2026, 1, 5))

    assert series["members"] == [(date(2026, 1, 10), 7.0)]


async def test_snapshot_use_case_collects_and_is_idempotent(uow_factory):
    # два профиля: один с очками, один пустой -> active_profiles == 1
    async with uow_factory() as uow:
        await uow.relationships.save(RelationshipProfile(user_id=1, guild_id=GUILD, points=30))
        await uow.relationships.save(RelationshipProfile(user_id=2, guild_id=GUILD, points=0))
        await uow.commit()

    snapshot = RecordDailySnapshotUseCase(uow_factory)
    day = date(2026, 7, 19)
    values = await snapshot.execute(GUILD, day, extra={MEMBERS: 8.0})

    assert values[POINTS_TOTAL] == 30
    assert values[ACTIVE_PROFILES] == 1
    assert values[MEMBERS] == 8.0
    assert values[WATCHLIST] == 0  # пустой киноклуб -> агрегат 0, не падает

    # повторный снапшот того же дня с другим member_count перезаписывает, не дублит
    await snapshot.execute(GUILD, day, extra={MEMBERS: 9.0})

    trends = await GetTrendsUseCase(uow_factory).execute(GUILD, day)
    assert trends[MEMBERS] == [(day, 9.0)]
    assert trends[POINTS_TOTAL] == [(day, 30.0)]
