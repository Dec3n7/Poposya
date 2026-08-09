"""Командный мост панель→бот: транспортно-независимая часть (enqueue + процессор).

Discord-исполнение здесь не проверяется (нужен живой бот) — только контракт:
кладём команду, процессор атомарно забирает, зовёт executor, пишет статус и
результат; повторный забор той же строки — no-op.
"""

import pytest
from sqlalchemy import select, update

from src.infrastructure.commands.bridge import (
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    Command,
    CommandError,
    CommandProcessor,
    _now,
    enqueue_command,
    get_status,
)
from src.infrastructure.db.models.commands import BotCommandModel

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


# --- восстановление зависших running-команд (lease) --------------------------

from datetime import timedelta  # noqa: E402


async def _ok_executor(_cmd: Command) -> str:
    return "ok"


async def _age_claim(session_factory, cmd_id: int, seconds: float) -> None:
    """Состарить lease команды — как будто её взяли в работу давно (эмуляция
    краша бота между claim и finish)."""
    async with session_factory() as session:
        await session.execute(
            update(BotCommandModel)
            .where(BotCommandModel.id == cmd_id)
            .values(claimed_at=_now() - timedelta(seconds=seconds))
        )
        await session.commit()


async def test_claim_sets_lease_and_increments_attempts(session_factory):
    proc = CommandProcessor(session_factory, _ok_executor)
    cmd_id = await enqueue_command(session_factory, GUILD, "mod.ban_perm", {"user_id": "1"}, 1)
    await proc._claim(cmd_id)
    async with session_factory() as session:
        row = await session.get(BotCommandModel, cmd_id)
        assert row.status == RUNNING
        assert row.attempts == 1
        assert row.claimed_at is not None
        assert row.worker_id


async def test_stale_running_is_requeued(session_factory):
    proc = CommandProcessor(session_factory, _ok_executor)
    cmd_id = await enqueue_command(session_factory, GUILD, "mod.ban_perm", {"user_id": "1"}, 1)
    await proc._claim(cmd_id)  # running, attempts=1
    await _age_claim(session_factory, cmd_id, 9999)  # lease протух — эмулируем краш
    assert await proc.recover_stale() == 1
    status, _ = await get_status(session_factory, cmd_id)
    assert status == PENDING  # вернулась в очередь, а не висит running навсегда


async def test_fresh_lease_is_not_recovered(session_factory):
    proc = CommandProcessor(session_factory, _ok_executor)
    cmd_id = await enqueue_command(session_factory, GUILD, "mod.mute", {"user_id": "1"}, 1)
    await proc._claim(cmd_id)  # lease только что — ещё живой исполнитель
    assert await proc.recover_stale() == 0
    status, _ = await get_status(session_factory, cmd_id)
    assert status == RUNNING  # свежий lease не трогаем


async def test_exhausted_attempts_marked_failed(session_factory):
    proc = CommandProcessor(session_factory, _ok_executor, max_attempts=2)
    cmd_id = await enqueue_command(session_factory, GUILD, "role.create", {"name": "x"}, 1)
    # первый прогон рухнул -> вернули в очередь (attempts=1)
    await proc._claim(cmd_id)
    await _age_claim(session_factory, cmd_id, 9999)
    assert await proc.recover_stale() == 1
    # второй прогон тоже рухнул (attempts=2) -> исчерпано, failed, не в очередь
    await proc._claim(cmd_id)
    await _age_claim(session_factory, cmd_id, 9999)
    assert await proc.recover_stale() == 0
    status, result = await get_status(session_factory, cmd_id)
    assert status == FAILED
    assert "перезапус" in result.lower()


async def test_process_pending_recovers_then_runs(session_factory):
    calls: list[Command] = []

    async def executor(cmd: Command) -> str:
        calls.append(cmd)
        return "готово"

    proc = CommandProcessor(session_factory, executor)
    cmd_id = await enqueue_command(session_factory, GUILD, "mod.unban", {"user_id": "5"}, 1)
    await proc._claim(cmd_id)  # эмулируем краш посреди исполнения: running
    await _age_claim(session_factory, cmd_id, 9999)
    handled = await proc.process_pending()  # recover -> pending -> исполнение
    assert handled == 1
    assert len(calls) == 1
    status, result = await get_status(session_factory, cmd_id)
    assert status == DONE and result == "готово"


async def test_legacy_running_without_lease_is_recovered(session_factory):
    # строка running с claimed_at=NULL — как до появления lease-полей
    async with session_factory() as session:
        session.add(
            BotCommandModel(
                guild_id=GUILD,
                command_type="mod.unban",
                payload='{"user_id": "1"}',
                status=RUNNING,
                requested_by=1,
                attempts=0,
                created_at=_now(),
            )
        )
        await session.commit()
        cmd_id = (
            await session.execute(select(BotCommandModel.id))
        ).scalars().first()
    proc = CommandProcessor(session_factory, _ok_executor)
    assert await proc.recover_stale() == 1
    status, _ = await get_status(session_factory, cmd_id)
    assert status == PENDING
