"""Слушатель Postgres LISTEN/NOTIFY — межпроцессная инвалидация кэша настроек.

Настройки серверов бот держит в памяти (GuildSettingsService) и читает без
похода в БД на горячих путях. Пока бот — единственный писатель, это безопасно.
С веб-панелью (отдельный процесс, та же Postgres) появляется второй писатель:
запись панели бот не увидит до рестарта.

Решение без новой инфраструктуры: запись настройки шлёт `pg_notify` с guild_id
(это делает GuildSettingsService в той же транзакции), а здесь бот держит
отдельное соединение asyncpg, слушает канал и перечитывает кэш этой гильдии.

Только PostgreSQL: на SQLite панель как второй процесс невозможна (один
писатель), поэтому фабрика возвращает None и листенер не запускается.
"""

import asyncio
import logging

import asyncpg

from src.infrastructure.guild_settings import SETTINGS_NOTIFY_CHANNEL, GuildSettingsService

logger = logging.getLogger(__name__)

_RECONNECT_DELAY = 3.0  # пауза перед переподключением после разрыва
_HEARTBEAT = 5.0  # как часто проверять, живо ли соединение


def _asyncpg_dsn(database_url: str) -> str:
    """postgresql+asyncpg://… -> postgresql://… — asyncpg.connect не понимает
    драйверный суффикс SQLAlchemy."""
    return database_url.replace("+asyncpg", "", 1)


def make_settings_listener(
    database_url: str, service: GuildSettingsService
) -> "SettingsChangeListener | None":
    """Листенер только для Postgres; на SQLite — None (в main не запускается)."""
    if not database_url.startswith("postgresql"):
        return None
    return SettingsChangeListener(_asyncpg_dsn(database_url), service)


class SettingsChangeListener:
    def __init__(self, dsn: str, service: GuildSettingsService):
        self._dsn = dsn
        self._service = service
        # держим ссылки на задачи перечитывания, чтобы их не собрал GC до конца
        self._tasks: set[asyncio.Task] = set()

    async def run_forever(self) -> None:
        while True:
            try:
                await self._listen()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Листенер настроек: соединение потеряно, переподключаюсь", exc_info=True
                )
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _listen(self) -> None:
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.add_listener(SETTINGS_NOTIFY_CHANNEL, self._on_notify)
            # NOTIFY, пришедшие пока соединения не было, не буферизуются —
            # после (пере)подключения синхронизируем весь кэш заново
            await self._service.load_all()
            logger.info("Листенер настроек: подключён, слушаю канал %s", SETTINGS_NOTIFY_CHANNEL)
            while not conn.is_closed():
                await asyncio.sleep(_HEARTBEAT)
        finally:
            await conn.close()

    def _on_notify(self, _conn, _pid: int, _channel: str, payload: str) -> None:
        # колбэк asyncpg — синхронный; перечитывание уводим в отдельную задачу,
        # чтобы не блокировать читающий цикл соединения и не ходить в БД по нему
        try:
            guild_id = int(payload)
        except (TypeError, ValueError):
            logger.warning("Листенер настроек: непонятный payload %r", payload)
            return
        task = asyncio.create_task(self._reload(guild_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _reload(self, guild_id: int) -> None:
        try:
            await self._service.reload_guild(guild_id)
            logger.info("Настройки гильдии %d перечитаны по NOTIFY", guild_id)
        except Exception:
            logger.exception("Листенер настроек: не удалось перечитать гильдию %d", guild_id)
