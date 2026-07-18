"""Командный мост панель→бот: транспортно-независимая часть (enqueue + процессор).

Discord-исполнение здесь не проверяется (нужен живой бот) — только контракт:
кладём команду, процессор атомарно забирает, зовёт executor, пишет статус и
результат; повторный забор той же строки — no-op.
"""

import pytest

from src.infrastructure.commands.bridge import (
    DONE,
    FAILED,
    PENDING,
    Command,
    CommandError,
    CommandProcessor,
    enqueue_command,
    get_status,
)

GUILD = 10


async def test_enqueue_writes_pending(session_factory):
    cmd_id = await enqueue_command(session_factory, GUILD, "mod.unban", {"user_id": "5"}, 1)
    status, result = await get_status(session_factory, cmd_id)
    assert status == PENDING and result is None


async def test_processor_runs_executor_and_marks_done(session_factory):
    seen: list[Command] = []

    async def executor(cmd: Command) -> str:
        seen.append(cmd)
        return "готово"

    proc = CommandProcessor(session_factory, executor)
    cmd_id = await enqueue_command(
        session_factory, GUILD, "mod.tempban", {"user_id": "42", "minutes": 60}, 7
    )
    await proc.process(cmd_id)

    assert len(seen) == 1
    assert seen[0].command_type == "mod.tempban"
    assert seen[0].payload == {"user_id": "42", "minutes": 60}
    assert seen[0].requested_by == 7
    status, result = await get_status(session_factory, cmd_id)
    assert status == DONE and result == "готово"


async def test_processor_marks_failed_on_command_error(session_factory):
    async def executor(_cmd: Command) -> str:
        raise CommandError("Нет права Ban Members.")

    proc = CommandProcessor(session_factory, executor)
    cmd_id = await enqueue_command(session_factory, GUILD, "mod.unban", {"user_id": "1"}, 1)
    await proc.process(cmd_id)

    status, result = await get_status(session_factory, cmd_id)
    assert status == FAILED and result == "Нет права Ban Members."


async def test_processor_claims_once(session_factory):
    calls = 0

    async def executor(_cmd: Command) -> str:
        nonlocal calls
        calls += 1
        return "ok"

    proc = CommandProcessor(session_factory, executor)
    cmd_id = await enqueue_command(session_factory, GUILD, "music.skip", {}, 1)
    await proc.process(cmd_id)
    await proc.process(cmd_id)  # уже done — второй забор ничего не делает

    assert calls == 1


async def test_process_pending_sweeps_all(session_factory):
    async def executor(_cmd: Command) -> str:
        return "ok"

    proc = CommandProcessor(session_factory, executor)
    ids = [
        await enqueue_command(session_factory, GUILD, "music.pause", {}, 1),
        await enqueue_command(session_factory, GUILD, "music.resume", {}, 1),
    ]
    handled = await proc.process_pending()
    assert handled == 2
    for cmd_id in ids:
        status, _ = await get_status(session_factory, cmd_id)
        assert status == DONE


@pytest.mark.parametrize("value", ["", "не-число"])
async def test_get_status_missing(session_factory, value):
    assert await get_status(session_factory, 999999) is None
