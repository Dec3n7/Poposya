"""Почасовые агрегаты активности: сообщения и войс-присутствие (поверх реального
SQLite через uow_factory) + сборка двух хитмапов в GetActivityStatsUseCase."""

from datetime import date

from src.application.message_activity.use_cases import GetActivityStatsUseCase

GUILD = 10
MONDAY = date(2026, 7, 20)  # опорная дата; weekday() берём из неё


async def test_add_voice_and_voice_hourly(uow_factory):
    async with uow_factory() as uow:
        await uow.message_activity.add_voice(GUILD, {(MONDAY, 12): 600, (MONDAY, 13): 300})
        await uow.commit()
    async with uow_factory() as uow:
        rows = await uow.message_activity.voice_hourly(GUILD, MONDAY)
    assert rows == [(MONDAY, 12, 600), (MONDAY, 13, 300)]


async def test_add_voice_increments_bucket(uow_factory):
    async with uow_factory() as uow:
        await uow.message_activity.add_voice(GUILD, {(MONDAY, 12): 600})
        await uow.commit()
    async with uow_factory() as uow:
        await uow.message_activity.add_voice(GUILD, {(MONDAY, 12): 300})
        await uow.commit()
    async with uow_factory() as uow:
        rows = await uow.message_activity.voice_hourly(GUILD, MONDAY)
    assert rows == [(MONDAY, 12, 900)]  # 600 + 300 (upsert seconds += delta)


async def test_voice_hourly_scoped_by_since(uow_factory):
    old = date(2026, 7, 1)
    async with uow_factory() as uow:
        await uow.message_activity.add_voice(GUILD, {(old, 10): 60, (MONDAY, 10): 120})
        await uow.commit()
    async with uow_factory() as uow:
        rows = await uow.message_activity.voice_hourly(GUILD, MONDAY)
    assert rows == [(MONDAY, 10, 120)]  # старое (< since) отсечено


async def test_add_voice_ignores_nonpositive(uow_factory):
    async with uow_factory() as uow:
        await uow.message_activity.add_voice(GUILD, {(MONDAY, 12): 0, (MONDAY, 13): -5})
        await uow.commit()
    async with uow_factory() as uow:
        rows = await uow.message_activity.voice_hourly(GUILD, MONDAY)
    assert rows == []


async def test_get_activity_stats_builds_both_heatmaps(uow_factory):
    async with uow_factory() as uow:
        await uow.message_activity.add(GUILD, {(MONDAY, 9): 5})
        await uow.message_activity.add_voice(GUILD, {(MONDAY, 9): 3600})  # 60 минут
        await uow.commit()
    stats = await GetActivityStatsUseCase(uow_factory).execute(GUILD, MONDAY)
    wd = MONDAY.weekday()
    assert stats.heatmap[wd][9] == 5  # сообщения как есть
    assert stats.voice_heatmap[wd][9] == 60  # секунды -> минуты
    assert sum(sum(row) for row in stats.voice_heatmap) == 60  # больше нигде не «протекло»


async def test_get_activity_stats_empty_guild(uow_factory):
    stats = await GetActivityStatsUseCase(uow_factory).execute(GUILD, MONDAY)
    assert stats.daily == []
    assert all(v == 0 for row in stats.heatmap for v in row)
    assert all(v == 0 for row in stats.voice_heatmap for v in row)
