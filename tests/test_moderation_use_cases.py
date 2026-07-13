"""Тесты сценариев модерации поверх реального UoW+SQLite: варны с порогом
мута, temp-бан с заменой и истечением."""

from datetime import datetime, timedelta, timezone

from src.application.moderation.use_cases import (
    ClearWarnsUseCase,
    GetWarnsUseCase,
    ListTempBansUseCase,
    PopExpiredBansUseCase,
    RemoveTempBanUseCase,
    TempBanUserUseCase,
    WarnUserUseCase,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


async def test_warn_accumulates_and_lists(uow_factory):
    warn = WarnUserUseCase(uow_factory, threshold=3)
    r1 = await warn.execute(1, 10, moderator_id=99, reason="спам", now=NOW)
    r2 = await warn.execute(1, 10, moderator_id=99, reason="флуд", now=NOW)
    assert (r1.count, r1.mute_triggered) == (1, False)
    assert (r2.count, r2.mute_triggered) == (2, False)

    warns = await GetWarnsUseCase(uow_factory).execute(1, 10)
    assert [w.reason for w in warns] == ["спам", "флуд"]
    assert warns[0].moderator_id == 99


async def test_warn_threshold_triggers_mute_and_resets(uow_factory):
    warn = WarnUserUseCase(uow_factory, threshold=2)
    await warn.execute(1, 10, moderator_id=99, reason="a", now=NOW)
    r2 = await warn.execute(1, 10, moderator_id=99, reason="b", now=NOW)
    assert r2.mute_triggered is True
    assert r2.count == 2
    # после мута счётчик обнулён
    assert await GetWarnsUseCase(uow_factory).execute(1, 10) == []


async def test_warns_isolated_per_guild(uow_factory):
    warn = WarnUserUseCase(uow_factory, threshold=5)
    await warn.execute(1, 10, moderator_id=99, reason="a", now=NOW)
    await warn.execute(1, 20, moderator_id=99, reason="b", now=NOW)
    assert len(await GetWarnsUseCase(uow_factory).execute(1, 10)) == 1
    assert len(await GetWarnsUseCase(uow_factory).execute(1, 20)) == 1


async def test_clear_warns_returns_count(uow_factory):
    warn = WarnUserUseCase(uow_factory, threshold=5)
    await warn.execute(1, 10, moderator_id=99, reason="a", now=NOW)
    await warn.execute(1, 10, moderator_id=99, reason="b", now=NOW)
    cleared = await ClearWarnsUseCase(uow_factory).execute(1, 10)
    assert cleared == 2
    assert await GetWarnsUseCase(uow_factory).execute(1, 10) == []


async def test_clear_warns_when_empty(uow_factory):
    assert await ClearWarnsUseCase(uow_factory).execute(1, 10) == 0


async def test_tempban_create_and_list_active(uow_factory):
    expires = await TempBanUserUseCase(uow_factory).execute(
        1, 10, moderator_id=99, reason="рейд", minutes=60, now=NOW
    )
    assert expires == NOW + timedelta(minutes=60)
    active = await ListTempBansUseCase(uow_factory).execute(10, NOW)
    assert len(active) == 1
    assert active[0].user_id == 1 and active[0].reason == "рейд"


async def test_tempban_replaces_previous(uow_factory):
    ban = TempBanUserUseCase(uow_factory)
    await ban.execute(1, 10, moderator_id=99, reason="first", minutes=30, now=NOW)
    await ban.execute(1, 10, moderator_id=99, reason="second", minutes=90, now=NOW)
    active = await ListTempBansUseCase(uow_factory).execute(10, NOW)
    assert len(active) == 1  # старая запись заменена
    assert active[0].reason == "second"


async def test_tempban_expired_not_listed_active(uow_factory):
    await TempBanUserUseCase(uow_factory).execute(
        1, 10, moderator_id=99, reason="x", minutes=10, now=NOW
    )
    later = NOW + timedelta(minutes=20)
    assert await ListTempBansUseCase(uow_factory).execute(10, later) == []


async def test_remove_tempban(uow_factory):
    await TempBanUserUseCase(uow_factory).execute(
        1, 10, moderator_id=99, reason="x", minutes=10, now=NOW
    )
    assert await RemoveTempBanUseCase(uow_factory).execute(1, 10) is True
    # повторное удаление — уже нечего
    assert await RemoveTempBanUseCase(uow_factory).execute(1, 10) is False


async def test_pop_expired_bans(uow_factory):
    ban = TempBanUserUseCase(uow_factory)
    await ban.execute(1, 10, moderator_id=99, reason="short", minutes=10, now=NOW)
    await ban.execute(2, 10, moderator_id=99, reason="long", minutes=120, now=NOW)
    later = NOW + timedelta(minutes=30)

    popped = await PopExpiredBansUseCase(uow_factory).execute(later)
    assert [b.user_id for b in popped] == [1]
    # повторный вызов — уже пусто, запись удалена
    assert await PopExpiredBansUseCase(uow_factory).execute(later) == []
    # долгий бан всё ещё активен
    assert len(await ListTempBansUseCase(uow_factory).execute(10, later)) == 1
