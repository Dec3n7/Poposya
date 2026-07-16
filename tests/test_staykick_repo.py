"""Репозиторий pending_kicks: планирование, отмена, созревание киков и
напоминаний (поверх реального SQLite)."""

from datetime import UTC, datetime, timedelta

from src.domain.staykick.entities import PendingKick

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def _pk(guild_id=10, user_id=1, remind_h=11, kick_h=12):
    return PendingKick(
        guild_id=guild_id,
        user_id=user_id,
        remind_at=NOW + timedelta(hours=remind_h),
        kick_at=NOW + timedelta(hours=kick_h),
        created_at=NOW,
    )


async def test_schedule_and_pop_due_kicks(uow_factory):
    async with uow_factory() as uow:
        await uow.pending_kicks.schedule(_pk())
        await uow.commit()
    # рано — ничего не созрело
    async with uow_factory() as uow:
        assert await uow.pending_kicks.pop_due_kicks(NOW) == []
        await uow.commit()
    # прошло 13ч — созрел и удалён
    async with uow_factory() as uow:
        due = await uow.pending_kicks.pop_due_kicks(NOW + timedelta(hours=13))
        await uow.commit()
    assert len(due) == 1 and due[0].user_id == 1
    async with uow_factory() as uow:
        assert await uow.pending_kicks.pop_due_kicks(NOW + timedelta(hours=20)) == []


async def test_cancel(uow_factory):
    async with uow_factory() as uow:
        await uow.pending_kicks.schedule(_pk())
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.pending_kicks.cancel(10, 1) is True
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.pending_kicks.cancel(10, 1) is False  # уже нет


async def test_schedule_replaces_previous(uow_factory):
    async with uow_factory() as uow:
        await uow.pending_kicks.schedule(_pk(kick_h=12))
        await uow.pending_kicks.schedule(_pk(kick_h=24))  # тот же (10,1)
        await uow.commit()
    async with uow_factory() as uow:
        due = await uow.pending_kicks.pop_due_kicks(NOW + timedelta(hours=30))
    assert len(due) == 1  # запись одна, не две


async def test_due_reminders_marks_once(uow_factory):
    async with uow_factory() as uow:
        await uow.pending_kicks.schedule(_pk(remind_h=0, kick_h=12))  # напомнить сразу
        await uow.commit()
    async with uow_factory() as uow:
        first = await uow.pending_kicks.due_reminders(NOW + timedelta(minutes=1))
        await uow.commit()
    assert len(first) == 1
    async with uow_factory() as uow:
        again = await uow.pending_kicks.due_reminders(NOW + timedelta(minutes=2))
        await uow.commit()
    assert again == []  # второй раз не напоминаем


async def test_due_reminders_skips_when_kick_already_due(uow_factory):
    async with uow_factory() as uow:
        await uow.pending_kicks.schedule(_pk(remind_h=0, kick_h=1))
        await uow.commit()
    async with uow_factory() as uow:
        # момент уже позже kick_at — напоминание не нужно, кик и так созрел
        due = await uow.pending_kicks.due_reminders(NOW + timedelta(hours=2))
    assert due == []
