"""Слушатель командного моста: Postgres LISTEN/NOTIFY + периодический sweep.

По NOTIFY (id команды) немедленно исполняет её через CommandProcessor. Плюс
раз в N секунд добирает зависшие pending — так мост переживает рестарт бота и
пропущенные во время разрыва соединения уведомления. Только Postgres; на
SQLite фабрика вернёт None (панель как второй писатель там не поднимается).
"""

import asyncio
import logging

import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infrastructure.commands.bridge import (
    COMMANDS_NOTIFY_CHANNEL,
    CommandProcessor,
    Executor,
)
from src.infrastructure.listener_health import ListenerHealth
from src.infrastructure.settings_listener import _asyncpg_dsn

logger = logging.getLogger(__name__)

_RECONNECT_DELAY = 3.0
_SWEEP_INTERVAL = 20.0  # добор зависших pending (durability)


def make_command_listener(
    database_url: str,
    session_factory: async_sessionmaker[AsyncSession],
    executor: Executor,
    non_idempotent: frozenset[str] = frozenset(),
) -> "CommandListener | None":
    if not database_url.startswith("postgresql"):
        return None
    processor = CommandProcessor(session_factory, executor, non_idempotent=non_idempotent)
    return CommandListener(_asyncpg_dsn(database_url), processor)


class CommandListener(ListenerHealth):
    def __init__(self, dsn: str, processor: CommandProcessor):
        super().__init__()
        self._dsn = dsn
        self._processor = processor
        self._tasks: set[asyncio.Task] = set()

    async def run_forever(self) -> None:
        sweep = asyncio.create_task(self._sweep_loop())
        try:
            while True:
                try:
                    await self._listen()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        "Листенер команд: соединение потеряно, переподключаюсь", exc_info=True
                    )
                    await asyncio.sleep(_RECONNECT_DELAY)
        finally:
            sweep.cancel()

    async def _listen(self) -> None:
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.add_listener(COMMANDS_NOTIFY_CHANNEL, self._on_notify)
            # NOTIFY, пришедшие пока соединения не было, не буферизуются — но их
            # добьёт sweep-цикл, поэтому просто подчищаем очередь при подключении
            await self._processor.process_pending()
            self.mark_connected()
            logger.info("Листенер команд: подключён, слушаю канал %s", COMMANDS_NOTIFY_CHANNEL)
            while not conn.is_closed():
                await asyncio.sleep(5.0)
        finally:
            self.mark_disconnected()
            await conn.close()

    def _on_notify(self, _conn, _pid: int, _channel: str, payload: str) -> None:
        try:
            cmd_id = int(payload)
        except (TypeError, ValueError):
            logger.warning("Листенер команд: непонятный payload %r", payload)
            return
        task = asyncio.create_task(self._processor.process(cmd_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(_SWEEP_INTERVAL)
            try:
                await self._processor.process_pending()
            except Exception:
                logger.exception("Листенер команд: sweep упал")
