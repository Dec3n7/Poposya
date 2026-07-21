"""CommandListener: транспортная часть моста панель→бот — Postgres LISTEN/NOTIFY
+ периодический sweep. Здесь всё без реального Postgres (asyncpg подменён
фейками): коннект/дисконнект, разбор NOTIFY, переподключение после сбоя, sweep.

Сквозной путь с настоящим Postgres — tests/test_command_listener_postgres.py.
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infrastructure.commands import listener as listener_module
from src.infrastructure.commands.bridge import COMMANDS_NOTIFY_CHANNEL, CommandProcessor
from src.infrastructure.commands.listener import CommandListener, make_command_listener


def test_make_command_listener_none_on_sqlite():
    assert make_command_listener("sqlite+aiosqlite:///x.db", MagicMock(), AsyncMock()) is None


def test_make_command_listener_builds_instance_on_postgres():
    executor = AsyncMock()
    result = make_command_listener("postgresql+asyncpg://u:p@host/db", MagicMock(), executor)

    assert isinstance(result, CommandListener)
    assert result._dsn == "postgresql://u:p@host/db"  # asyncpg не понимает +asyncpg
    assert isinstance(result._processor, CommandProcessor)
    assert result._processor._executor is executor


async def test_on_notify_schedules_process():
    processor = MagicMock()
    processor.process = AsyncMock()
    cl = CommandListener("dsn", processor)

    cl._on_notify(None, 1, "chan", "42")
    await asyncio.sleep(0)  # дать созданной задаче выполниться
    await asyncio.sleep(0)  # и ещё цикл, чтобы отработал done_callback

    processor.process.assert_awaited_once_with(42)
    assert cl._tasks == set()  # done_callback вычистил сам себя


async def test_on_notify_ignores_bad_payload():
    processor = MagicMock()
    processor.process = AsyncMock()
    cl = CommandListener("dsn", processor)

    cl._on_notify(None, 1, "chan", "не-число")
    await asyncio.sleep(0)

    processor.process.assert_not_awaited()


class FakeConn:
    def __init__(self, closed_after=0):
        self._checked = 0
        self._closed_after = closed_after
        self.add_listener = AsyncMock()
        self.close = AsyncMock()

    def is_closed(self):
        self._checked += 1
        return self._checked > self._closed_after


async def test_listen_connects_registers_and_closes(monkeypatch):
    conn = FakeConn(closed_after=0)  # is_closed() сразу True -> heartbeat-цикл не крутится
    connect = AsyncMock(return_value=conn)
    monkeypatch.setattr(listener_module.asyncpg, "connect", connect)
    processor = MagicMock()
    processor.process_pending = AsyncMock(return_value=0)
    cl = CommandListener("dsn", processor)

    await cl._listen()

    connect.assert_awaited_once_with("dsn")
    conn.add_listener.assert_awaited_once_with(COMMANDS_NOTIFY_CHANNEL, cl._on_notify)
    processor.process_pending.assert_awaited_once()
    conn.close.assert_awaited_once()


async def test_listen_heartbeats_until_closed(monkeypatch):
    conn = FakeConn(closed_after=2)  # два тика heartbeat, третья проверка -> закрыт
    connect = AsyncMock(return_value=conn)
    monkeypatch.setattr(listener_module.asyncpg, "connect", connect)
    sleeps: list[float] = []

    async def fast_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(listener_module.asyncio, "sleep", fast_sleep)
    processor = MagicMock()
    processor.process_pending = AsyncMock(return_value=0)
    cl = CommandListener("dsn", processor)

    await cl._listen()

    assert sleeps == [5.0, 5.0]  # ровно столько тиков, сколько conn был открыт
    conn.close.assert_awaited_once()


async def test_listen_closes_conn_even_if_setup_fails(monkeypatch):
    conn = FakeConn()
    conn.add_listener = AsyncMock(side_effect=RuntimeError("boom"))
    connect = AsyncMock(return_value=conn)
    monkeypatch.setattr(listener_module.asyncpg, "connect", connect)
    cl = CommandListener("dsn", MagicMock())

    with pytest.raises(RuntimeError):
        await cl._listen()

    conn.close.assert_awaited_once()


async def test_run_forever_reconnects_after_error_then_propagates_cancel(monkeypatch):
    monkeypatch.setattr(listener_module, "_RECONNECT_DELAY", 0)
    cl = CommandListener("dsn", MagicMock())

    async def idle_sweep():
        await asyncio.sleep(100)

    cl._sweep_loop = idle_sweep  # реальный sweep этому тесту не нужен
    calls = 0

    async def flaky_listen():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("connection lost")
        raise asyncio.CancelledError()

    cl._listen = flaky_listen

    with pytest.raises(asyncio.CancelledError):
        await cl.run_forever()
    await asyncio.sleep(0)  # дать отменённой sweep-задаче доработать

    assert calls == 2  # первый обрыв поймали и переподключились, второй — отмена


async def test_sweep_loop_calls_process_pending_and_survives_errors(monkeypatch):
    monkeypatch.setattr(listener_module, "_SWEEP_INTERVAL", 0)
    processor = MagicMock()
    calls = 0

    async def flaky_pending():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("db hiccup")
        return 0

    processor.process_pending = AsyncMock(side_effect=flaky_pending)
    cl = CommandListener("dsn", processor)

    task = asyncio.create_task(cl._sweep_loop())
    try:
        deadline = asyncio.get_event_loop().time() + 2.0
        while calls < 2 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert calls >= 2  # пережил RuntimeError на первой итерации и продолжил
