"""Сквозной путь командного моста: LISTEN/NOTIFY на настоящем Postgres.

Только PostgreSQL: механизм на NOTIFY, на SQLite панель как второй процесс не
поднимается (см. tests/test_command_bridge.py — там протестирована
транспортно-независимая часть на любой БД).
"""

import asyncio
import contextlib
import os

import pytest

from src.infrastructure.commands.bridge import DONE, FAILED, Command, enqueue_command, get_status
from src.infrastructure.commands.listener import make_command_listener

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="LISTEN/NOTIFY есть только в Postgres; на SQLite второго писателя нет",
)

GUILD = 10


async def _wait_until(predicate, timeout: float = 4.0, step: float = 0.05) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(step)
    return predicate()


async def test_notify_delivers_command_to_executor(session_factory):
    """Панель кладёт команду -> листенер получает pg_notify и выполняет её сам,
    без ожидания периодического sweep."""
    url = os.environ["TEST_DATABASE_URL"]
    seen: list[Command] = []

    async def executor(cmd: Command) -> str:
        seen.append(cmd)
        return "готово"

    listener = make_command_listener(url, session_factory, executor)
    assert listener is not None
    task = asyncio.create_task(listener.run_forever())
    try:
        await asyncio.sleep(1.0)  # дать листенеру подключиться и сделать LISTEN
        cmd_id = await enqueue_command(session_factory, GUILD, "music.skip", {"foo": "bar"}, 7)
        got = await _wait_until(lambda: len(seen) == 1)
        assert got, "листенер не получил NOTIFY о новой команде"
        assert seen[0].command_type == "music.skip"
        assert seen[0].requested_by == 7
        status, result = await get_status(session_factory, cmd_id)
        assert (status, result) == (DONE, "готово")
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_notify_failure_marks_command_failed(session_factory):
    url = os.environ["TEST_DATABASE_URL"]

    async def executor(_cmd: Command) -> str:
        raise RuntimeError("boom")

    listener = make_command_listener(url, session_factory, executor)
    task = asyncio.create_task(listener.run_forever())
    try:
        await asyncio.sleep(1.0)
        cmd_id = await enqueue_command(session_factory, GUILD, "mod.unban", {"user_id": "1"}, 1)
        # get_status асинхронный, поэтому опрашиваем вручную вместо _wait_until
        # (та ждёт синхронный предикат)
        status = None
        deadline = asyncio.get_event_loop().time() + 4.0
        while asyncio.get_event_loop().time() < deadline:
            status, _ = await get_status(session_factory, cmd_id)
            if status in (DONE, FAILED):
                break
            await asyncio.sleep(0.05)
        assert status == FAILED
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
