"""Слушатель Postgres NOTIFY — межпроцессная инвалидация кэша тарифов.

Тот же паттерн, что у настроек (`settings_listener`): панель, выдав/сняв подписку
(отдельный процесс, та же Postgres), шлёт `pg_notify` с guild_id, а бот здесь
держит соединение asyncpg, слушает канал и перечитывает тариф этой гильдии.

Только PostgreSQL: на SQLite панель как второй писатель невозможна — фабрика
возвращает None и листенер не запускается."""

import asyncio
import logging

import asyncpg

from src.infrastructure.entitlements import ENTITLEMENTS_NOTIFY_CHANNEL, EntitlementService
from src.infrastructure.listener_health import ListenerHealth

logger = logging.getLogger(__name__)

_RECONNECT_DELAY = 3.0
_HEARTBEAT = 5.0


def _asyncpg_dsn(database_url: str) -> str:
    return database_url.replace("+asyncpg", "", 1)


def make_entitlements_listener(
    database_url: str, service: EntitlementService
) -> "EntitlementChangeListener | None":
    if not database_url.startswith("postgresql"):
        return None
    return EntitlementChangeListener(_asyncpg_dsn(database_url), service)


class EntitlementChangeListener(ListenerHealth):
    def __init__(self, dsn: str, service: EntitlementService):
        super().__init__()
        self._dsn = dsn
        self._service = service
        self._tasks: set[asyncio.Task] = set()

    async def run_forever(self) -> None:
        while True:
            try:
                await self._listen()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Листенер тарифов: соединение потеряно, переподключаюсь", exc_info=True
                )
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _listen(self) -> None:
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.add_listener(ENTITLEMENTS_NOTIFY_CHANNEL, self._on_notify)
            # NOTIFY за время без соединения не буферизуются — пересинхронизируем весь кэш
            await self._service.load_all()
            self.mark_connected()
            logger.info(
                "Листенер тарифов: подключён, слушаю канал %s", ENTITLEMENTS_NOTIFY_CHANNEL
            )
            while not conn.is_closed():
                await asyncio.sleep(_HEARTBEAT)
        finally:
            self.mark_disconnected()
            await conn.close()

    def _on_notify(self, _conn, _pid: int, _channel: str, payload: str) -> None:
        try:
            guild_id = int(payload)
        except (TypeError, ValueError):
            logger.warning("Листенер тарифов: непонятный payload %r", payload)
            return
        task = asyncio.create_task(self._reload(guild_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _reload(self, guild_id: int) -> None:
        try:
            await self._service.reload_guild(guild_id)
            logger.info("Тариф гильдии %d перечитан по NOTIFY", guild_id)
        except Exception:
            logger.exception("Листенер тарифов: не удалось перечитать гильдию %d", guild_id)
