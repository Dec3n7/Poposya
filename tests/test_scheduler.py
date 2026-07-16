"""DeferredScheduler: именованные одноразовые asyncio-таймеры."""

import asyncio
from datetime import UTC, datetime, timedelta

from src.infrastructure.discord.scheduler import DeferredScheduler


def _past():
    return datetime.now(UTC) - timedelta(seconds=1)


def _future():
    return datetime.now(UTC) + timedelta(seconds=100)


async def test_overdue_runs_immediately():
    fired = []
    sched = DeferredScheduler()
    sched.schedule("k", _past(), lambda: _mark(fired))
    await asyncio.sleep(0.02)
    assert fired == [1]
    assert len(sched) == 0  # завершившийся таймер убирается из реестра


async def test_future_not_yet_fired_and_cancel():
    fired = []
    sched = DeferredScheduler()
    sched.schedule("k", _future(), lambda: _mark(fired))
    assert len(sched) == 1
    sched.cancel("k")
    await asyncio.sleep(0.02)
    assert fired == []
    assert len(sched) == 0


async def test_reschedule_replaces_previous():
    fired = []
    sched = DeferredScheduler()
    sched.schedule("k", _future(), lambda: _mark(fired, tag="old"))
    sched.schedule("k", _past(), lambda: _mark(fired, tag="new"))  # тот же ключ
    await asyncio.sleep(0.02)
    assert fired == ["new"]  # старый таймер отменён, сработал только новый


async def test_cancel_all():
    sched = DeferredScheduler()
    sched.schedule("a", _future(), lambda: _noop())
    sched.schedule("b", _future(), lambda: _noop())
    assert len(sched) == 2
    sched.cancel_all()
    assert len(sched) == 0


async def test_callback_exception_does_not_leak():
    sched = DeferredScheduler()
    sched.schedule("bad", _past(), lambda: _boom())
    await asyncio.sleep(0.02)  # исключение внутри callback только логируется
    assert len(sched) == 0


async def _mark(store, tag=1):
    store.append(tag)


async def _noop():
    return None


async def _boom():
    raise RuntimeError("boom")
