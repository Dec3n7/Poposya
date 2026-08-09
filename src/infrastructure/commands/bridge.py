"""Командный мост панель→бот: транспортно-независимая часть.

Панель кладёт команду в `bot_commands` и (на Postgres) шлёт pg_notify в той же
транзакции. Бот забирает команду через `CommandProcessor` — по NOTIFY или
периодическим sweep'ом (durable: переживает рестарт бота). Executor выполняет
конкретное Discord-действие и возвращает строку-результат либо кидает
`CommandError` с человеческим текстом.

Только Postgres даёт межпроцессную доставку (NOTIFY). На SQLite (dev, один
процесс) enqueue просто пишет строку без notify — панель как второй процесс там
и не поднимается.
"""

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infrastructure.db.dml import rows_affected
from src.infrastructure.db.models.commands import BotCommandModel

logger = logging.getLogger(__name__)

COMMANDS_NOTIFY_CHANNEL = "poposya_commands"

# терминальные и промежуточные статусы
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
_TERMINAL = {DONE, FAILED}

# lease: сколько команда может числиться 'running' до того, как sweep сочтёт
# исполнителя мёртвым (краш бота между claim и finish) и вернёт её в очередь.
# Больше самого долгого реального действия (массовая выдача ролей — секунды),
# с запасом; меньше — риск повторно запустить ещё живую команду.
LEASE_TIMEOUT_SECONDS = 120
# сколько всего раз команду можно взять в работу. attempts растёт на каждом
# claim; recover_stale возвращает в очередь только пока attempts < этого числа,
# иначе помечает failed. 2 = один повторный прогон после краша. Держим малым
# намеренно: часть действий (role.create, music.skip) не идемпотентна, и один
# повтор — приемлемый максимум дублирования (большинство хендлеров сверяют
# состояние сами: role.assign/unassign, pause/resume, import/preset).
DEFAULT_MAX_ATTEMPTS = 2

# идентификатор процесса-исполнителя (диагностика в worker_id lease). Уникален
# на запуск: после рестарта — новый, что и отражает смену владельца lease.
_WORKER_ID = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"


class CommandError(Exception):
    """Ожидаемый провал выполнения команды: текст показывается админу в панели
    (нет прав, участник не найден, ничего не играет и т. п.)."""


@dataclass(frozen=True)
class Command:
    id: int
    guild_id: int
    command_type: str
    payload: dict
    requested_by: int


# executor: (команда) -> строка-результат; кидает CommandError на ожидаемый провал
Executor = Callable[[Command], Awaitable[str]]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def enqueue_command(
    session_factory: async_sessionmaker[AsyncSession],
    guild_id: int,
    command_type: str,
    payload: dict,
    requested_by: int,
) -> int:
    """Кладёт команду (status=pending) и шлёт pg_notify в той же транзакции.
    Возвращает id для последующего опроса результата."""
    async with session_factory() as session:
        row = BotCommandModel(
            guild_id=guild_id,
            command_type=command_type,
            payload=json.dumps(payload, ensure_ascii=False),
            status=PENDING,
            requested_by=requested_by,
            attempts=0,
            created_at=_now(),
        )
        session.add(row)
        await session.flush()  # получить id до notify
        cmd_id = row.id
        bind = session.bind
        if bind is not None and bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT pg_notify(:channel, :payload)"),
                {"channel": COMMANDS_NOTIFY_CHANNEL, "payload": str(cmd_id)},
            )
        await session.commit()
        return cmd_id


async def get_status(
    session_factory: async_sessionmaker[AsyncSession], cmd_id: int
) -> tuple[str, str | None] | None:
    """(status, result) команды или None, если её нет."""
    async with session_factory() as session:
        row = await session.get(BotCommandModel, cmd_id)
        if row is None:
            return None
        return row.status, row.result


async def wait_for_result(
    session_factory: async_sessionmaker[AsyncSession],
    cmd_id: int,
    timeout: float = 5.0,
    interval: float = 0.15,
) -> tuple[str, str | None]:
    """Опрашивает строку до терминального статуса или таймаута. По таймауту
    вернёт текущий (обычно pending/running) — панель покажет «применяется»."""
    deadline = asyncio.get_running_loop().time() + timeout
    last: tuple[str, str | None] = (PENDING, None)
    while True:
        current = await get_status(session_factory, cmd_id)
        if current is not None:
            last = current
            if current[0] in _TERMINAL:
                return current
        if asyncio.get_running_loop().time() >= deadline:
            return last
        await asyncio.sleep(interval)


class CommandProcessor:
    """Забирает pending-команды и выполняет их через executor. Атомарный
    claim (`UPDATE ... WHERE status=pending`) защищает от двойного исполнения,
    если NOTIFY и sweep сойдутся на одной строке.

    Claim ставит lease (claimed_at, worker_id, attempts+1). Если бот падает
    между claim и finish, команда остаётся 'running' — `recover_stale` по
    истечении lease возвращает её в очередь (или помечает failed, исчерпав
    attempts), иначе она висела бы 'running' вечно."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        executor: Executor,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        lease_timeout: float = LEASE_TIMEOUT_SECONDS,
    ):
        self._session_factory = session_factory
        self._executor = executor
        self._max_attempts = max_attempts
        self._lease_timeout = lease_timeout

    async def _claim(self, cmd_id: int) -> Command | None:
        """Переводит pending->running атомарно и ставит lease; None — уже
        забрана/нет."""
        async with self._session_factory() as session:
            result = await session.execute(
                update(BotCommandModel)
                .where(BotCommandModel.id == cmd_id, BotCommandModel.status == PENDING)
                .values(
                    status=RUNNING,
                    claimed_at=_now(),
                    worker_id=_WORKER_ID,
                    attempts=BotCommandModel.attempts + 1,
                    updated_at=_now(),
                )
            )
            if rows_affected(result) == 0:
                await session.rollback()
                return None
            row = await session.get(BotCommandModel, cmd_id)
            await session.commit()
            if row is None:
                return None
            return Command(
                id=row.id,
                guild_id=row.guild_id,
                command_type=row.command_type,
                payload=json.loads(row.payload),
                requested_by=row.requested_by,
            )

    async def recover_stale(self) -> int:
        """Вернуть в очередь команды с просроченным lease (бот упал между claim
        и finish). Просроченные, но не исчерпавшие attempts — обратно в pending;
        исчерпавшие — в failed (чтобы не крутить бесконечно). claimed_at IS NULL
        + running — legacy-строки до появления lease: тоже считаем зависшими.
        Возвращает число фактически возвращённых в очередь."""
        cutoff = _now() - timedelta(seconds=self._lease_timeout)
        async with self._session_factory() as session:
            stmt = (
                select(BotCommandModel.id, BotCommandModel.attempts)
                .where(
                    BotCommandModel.status == RUNNING,
                    or_(
                        BotCommandModel.claimed_at.is_(None),
                        BotCommandModel.claimed_at < cutoff,
                    ),
                )
                .order_by(BotCommandModel.id)
                .limit(100)
            )
            rows = list((await session.execute(stmt)).all())

        requeued = 0
        for cmd_id, attempts in rows:
            if attempts >= self._max_attempts:
                # исчерпаны попытки: терминальный failed. Guard status==RUNNING —
                # не затираем результат, если исполнитель как раз finish'нул
                await self._finish_if_running(
                    cmd_id, FAILED, "Не удалось подтвердить выполнение (бот перезапускался)."
                )
                logger.warning(
                    "Команда %s зависла в running и исчерпала попытки — помечена failed", cmd_id
                )
            else:
                async with self._session_factory() as session:
                    res = await session.execute(
                        update(BotCommandModel)
                        .where(BotCommandModel.id == cmd_id, BotCommandModel.status == RUNNING)
                        .values(status=PENDING, claimed_at=None, worker_id=None, updated_at=_now())
                    )
                    await session.commit()
                    if rows_affected(res):
                        requeued += 1
                        logger.info("Команда %s с просроченным lease возвращена в очередь", cmd_id)
        return requeued

    async def _finish_if_running(self, cmd_id: int, status: str, result: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(BotCommandModel)
                .where(BotCommandModel.id == cmd_id, BotCommandModel.status == RUNNING)
                .values(status=status, result=result[:2000], updated_at=_now())
            )
            await session.commit()

    async def _finish(self, cmd_id: int, status: str, result: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(BotCommandModel)
                .where(BotCommandModel.id == cmd_id)
                .values(status=status, result=result[:2000], updated_at=_now())
            )
            await session.commit()

    async def process(self, cmd_id: int) -> None:
        command = await self._claim(cmd_id)
        if command is None:
            return  # уже обработана или в работе
        try:
            result = await self._executor(command)
        except CommandError as exc:
            logger.info("Команда %s (%s) отклонена: %s", cmd_id, command.command_type, exc)
            await self._finish(cmd_id, FAILED, str(exc))
        except Exception:
            logger.exception("Команда %s (%s) упала", cmd_id, command.command_type)
            await self._finish(cmd_id, FAILED, "Внутренняя ошибка выполнения")
        else:
            await self._finish(cmd_id, DONE, result)

    async def process_pending(self) -> int:
        """Sweep: сперва вернуть в очередь команды с протухшим lease (durability
        после краша), затем забрать все pending (рестарт бота или пропущенный
        NOTIFY). Возвращает число обработанных pending."""
        await self.recover_stale()
        async with self._session_factory() as session:
            stmt = (
                select(BotCommandModel.id)
                .where(BotCommandModel.status == PENDING)
                .order_by(BotCommandModel.id)
                .limit(100)
            )
            ids = list((await session.execute(stmt)).scalars().all())
        for cmd_id in ids:
            await self.process(cmd_id)
        return len(ids)
