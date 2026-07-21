"""Слушатель Postgres LISTEN/NOTIFY для персон — межпроцессная инвалидация кэша.

Полный аналог settings_listener: веб-панель (отдельный процесс, та же Postgres)
меняет персону и шлёт pg_notify в канал poposya_personas; бот здесь держит
отдельное соединение asyncpg, слушает канал и перечитывает персоны целиком
(PersonaService.reload). Персон немного, поэтому reload — полный, без разбора
payload.

Только PostgreSQL: на SQLite второго писателя нет — фабрика возвращает None и
листенер не запускается.
"""

import asyncio
import logging

import asyncpg

from src.infrastructure.persona_service import PERSONAS_NOTIFY_CHANNEL, PersonaService

logger = logging.getLogger(__name__)

_RECONNECT_DELAY = 3.0
_HEARTBEAT = 5.0


def _asyncpg_dsn(database_url: str) -> str:
    """postgresql+asyncpg://… -> postgresql://… (asyncpg.connect не понимает
    драйверный суффикс SQLAlchemy)."""
    return database_url.replace("+asyncpg", "", 1)


def make_persona_listener(
    database_url: str, service: PersonaService
) -> "PersonaChangeListener | None":
    if not database_url.startswith("postgresql"):
        return None
    return PersonaChangeListener(_asyncpg_dsn(database_url), service)


class PersonaChangeListener:
    def __init__(self, dsn: str, service: PersonaService):
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
                    "Листенер персон: соединение потеряно, переподключаюсь", exc_info=True
                )
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _listen(self) -> None:
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.add_listener(PERSONAS_NOTIFY_CHANNEL, self._on_notify)
            # NOTIFY, пришедшие пока соединения не было, не буферизуются —
            # после (пере)подключения синхронизируем весь кэш заново
            await self._service.reload()
            logger.info("Листенер персон: подключён, слушаю канал %s", PERSONAS_NOTIFY_CHANNEL)
            while not conn.is_closed():
                await asyncio.sleep(_HEARTBEAT)
        finally:
            await conn.close()

    def _on_notify(self, _conn, _pid: int, _channel: str, _payload: str) -> None:
        # колбэк asyncpg синхронный — перечитывание уводим в отдельную задачу,
        # чтобы не блокировать читающий цикл соединения
        task = asyncio.create_task(self._reload())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _reload(self) -> None:
        try:
            await self._service.reload()
            logger.info("Персоны перечитаны по NOTIFY")
        except Exception:
            logger.exception("Листенер персон: не удалось перечитать персоны")
