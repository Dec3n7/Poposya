"""Use-cases pending_kicks: расчёт времён, отмена."""

from datetime import UTC, datetime, timedelta

from src.application.staykick.use_cases import (
    CancelPendingKickUseCase,
    SchedulePendingKickUseCase,
)

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


async def test_schedule_computes_kick_and_remind(uow_factory):
    kick_at = await SchedulePendingKickUseCase(uow_factory).execute(
        10, 1, NOW, hours=12, remind_before_minutes=60
    )
    assert kick_at == NOW + timedelta(hours=12)
    async with uow_factory() as uow:
        # напоминание за час доступно в 11:00 после now
        assert len(await uow.pending_kicks.due_reminders(NOW + timedelta(hours=11, minutes=1))) == 1


async def test_schedule_short_window_reminds_immediately(uow_factory):
    # окно (1ч) короче напоминания (2ч) — remind_at зажат в now
    await SchedulePendingKickUseCase(uow_factory).execute(
        10, 1, NOW, hours=1, remind_before_minutes=120
    )
    async with uow_factory() as uow:
        assert len(await uow.pending_kicks.due_reminders(NOW + timedelta(minutes=1))) == 1


async def test_cancel_use_case(uow_factory):
    await SchedulePendingKickUseCase(uow_factory).execute(10, 1, NOW, 12, 60)
    assert await CancelPendingKickUseCase(uow_factory).execute(10, 1) is True
    assert await CancelPendingKickUseCase(uow_factory).execute(10, 1) is False
