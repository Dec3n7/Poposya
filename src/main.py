import asyncio
import logging
import time
from typing import cast

import discord
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.application.di.root_container import build_root_container
from src.application.interfaces.ai_provider import IAIProvider
from src.config import Settings

# main — композиционный корень: поля RootContainer типизированы как object,
# чтобы application не тянул infrastructure (ARCHITECTURE.md); здесь сужаем к
# реальным типам через cast, т.к. entrypoint уже на уровне infrastructure
from src.infrastructure.db.backup import make_backup_service
from src.infrastructure.diagnostics import log_boot_summary, probe_dependencies
from src.infrastructure.discord.client import PoposyaBot
from src.infrastructure.entitlements import EntitlementService
from src.infrastructure.entitlements_listener import EntitlementChangeListener
from src.infrastructure.events.outbox import OutboxDispatcher
from src.infrastructure.guild_settings import GuildSettingsService
from src.infrastructure.listener_health import ListenerHealth
from src.infrastructure.logging.error_counter import install_error_counter
from src.infrastructure.logging.json_formatter import setup_logging
from src.infrastructure.persona_listener import PersonaChangeListener
from src.infrastructure.persona_service import PersonaService
from src.infrastructure.settings_listener import SettingsChangeListener
from src.infrastructure.web.app import HealthChecker, measure_event_loop_lag, start_health_server

logger = logging.getLogger(__name__)
boot = logging.getLogger("boot")


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")


class DatabaseProbe:
    """Проверка БД и замер её латентности одним запросом.

    Ворота и метрика питаются от одного `SELECT 1`: `/health/full` дёргает
    сначала `check`, затем `metrics`, и второй поход в БД ради того же числа был
    бы лишней нагрузкой ровно в тот момент, когда база уже страдает.
    """

    def __init__(self, engine):
        self._engine = engine
        self._last_latency_ms: float | None = None
        self._last_ok: bool | None = None

    async def check(self) -> bool:
        started = time.perf_counter()
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            self._last_latency_ms = None
            self._last_ok = False
            raise
        self._last_latency_ms = round((time.perf_counter() - started) * 1000, 2)
        self._last_ok = True
        return True

    async def metrics(self) -> dict:
        pool = getattr(self._engine, "pool", None)
        stats: dict = {"latency_ms": self._last_latency_ms, "reachable": self._last_ok}
        # у SQLite-пула этих методов нет — метрика опциональна, а не обязательна
        for key, attr in (("pool_size", "size"), ("pool_checked_out", "checkedout")):
            getter = getattr(pool, attr, None)
            if getter is not None:
                try:
                    stats[key] = getter()
                except Exception:
                    pass
        return stats


def _discord_check(bot: PoposyaBot):
    async def check() -> bool:
        return bot.is_ready()

    return check


def _background_tasks_metrics(tasks: dict[str, asyncio.Task]):
    """Живы ли фоновые задачи.

    Самая важная метрика набора. Задачи запускаются через `create_task` и, если
    какая-то упадёт, исключение осядет внутри объекта Task: бот продолжит
    работать, `/health` останется зелёным, а функция (доставка событий, мост
    панели, бэкапы) будет мертва до ближайшего рестарта.
    """

    async def collect() -> dict:
        dead: dict[str, str] = {}
        for name, task in tasks.items():
            if not task.done():
                continue
            if task.cancelled():
                dead[name] = "cancelled"
                continue
            exc = task.exception()
            dead[name] = f"{type(exc).__name__}: {exc}" if exc else "finished"
        return {
            "expected": len(tasks),
            "alive": len(tasks) - len(dead),
            "dead": dead,
            "names": sorted(tasks),
        }

    return collect


def _listeners_metrics(listeners: dict[str, object]):
    async def collect() -> dict:
        return {
            name: cast(ListenerHealth, obj).health_snapshot() for name, obj in listeners.items()
        }

    return collect


async def _as_async(value: dict) -> dict:
    """Готовый синхронный слепок в обёртке корутины — реестр метрик асинхронный,
    а latency шлюза и список когов считаются мгновенно и без ожидания."""
    return value


async def _runtime_metrics() -> dict:
    return {
        "event_loop_lag_ms": await measure_event_loop_lag(),
        "asyncio_tasks": len(asyncio.all_tasks()),
    }


async def run() -> None:
    settings = Settings()  # type: ignore[call-arg]  # pydantic-settings читает поля из env
    setup_logging(settings.log_level, settings.log_format, settings.log_file)
    # строго после setup_logging: он чистит root.handlers
    error_counter = install_error_counter()
    boot.info("Запуск Попоси…")

    if settings.auto_migrate:
        # alembic — синхронный; env.py сам поднимает async-движок,
        # поэтому запускаем в отдельном потоке без активного event loop
        await asyncio.get_running_loop().run_in_executor(None, _run_migrations)
        logger.info("Миграции применены")

    # сводка «что включилось / что отключено» + проверка связи с внешними
    # сервисами (Groq, провайдеры фильмов) — сбои не фатальны, только в логах
    log_boot_summary(settings)
    await probe_dependencies(settings)

    container = build_root_container(settings)
    # сужаем object-поля контейнера к реальным типам (см. импорт-блок выше)
    guild_settings = cast(GuildSettingsService, container.guild_settings)
    persona = cast(PersonaService, container.persona)
    engine = cast(AsyncEngine, container.engine)
    session_factory = cast("async_sessionmaker[AsyncSession]", container.session_factory)
    outbox_dispatcher = cast(OutboxDispatcher, container.outbox_dispatcher)
    # пер-гильдийные настройки (/config) — поднять переопределения в память
    await guild_settings.load_all()
    # персоны (текст/личность бота) — поднять в память (в проде сид «Попоси» уже
    # в БД из миграции 0031; иначе load_all создаст дефолт-строку идемпотентно)
    await persona.load_all()
    # тарифы серверов (монетизация) — поднять подписки в память
    entitlements = cast(EntitlementService, container.entitlements)
    await entitlements.load_all()
    bot = PoposyaBot(container)

    # командный мост панель→бот: панель кладёт команду в bot_commands + NOTIFY,
    # бот исполняет реальное Discord-действие (бан/мут/музыка). Только Postgres.
    from src.infrastructure.commands.listener import make_command_listener
    from src.infrastructure.discord.command_executor import (
        NON_IDEMPOTENT_COMMANDS,
        DiscordCommandExecutor,
    )

    command_executor = DiscordCommandExecutor(bot, container.moderation)
    command_listener = make_command_listener(
        settings.database_url,
        session_factory,
        command_executor.execute,
        non_idempotent=NON_IDEMPOTENT_COMMANDS,
    )

    # реестр фоновых задач: заполняется ниже, но метрика ссылается на него уже
    # сейчас — health-сервер поднимается раньше задач и не должен их ждать
    background: dict[str, asyncio.Task] = {}
    listeners: dict[str, object] = {}

    db_probe = DatabaseProbe(container.engine)
    health = HealthChecker()
    # ворота — булевы, их читает /health и docker healthcheck (контракт прежний)
    health.register("database", db_probe.check)
    health.register("discord", _discord_check(bot))
    # метрики — сырые числа для WARDEN на /health/full; порогов здесь нет,
    # интерпретация целиком на стороне мониторинга
    health.register_metric("gateway", lambda: _as_async(bot.gateway_stats()))
    health.register_metric("cogs", lambda: _as_async(bot.cogs_stats()))
    health.register_metric("database", db_probe.metrics)
    health.register_metric("outbox", outbox_dispatcher.backlog_stats)
    health.register_metric("background_tasks", _background_tasks_metrics(background))
    health.register_metric("listeners", _listeners_metrics(listeners))
    health.register_metric("errors", lambda: _as_async(error_counter.snapshot()))
    health.register_metric("runtime", _runtime_metrics)
    health_runner = await start_health_server(
        health, settings.health_port, settings.health_full_token
    )
    backup = make_backup_service(
        settings.database_url,
        settings.backup_dir,
        settings.backup_interval_hours,
        settings.backup_keep,
        settings.backup_offsite_cmd,
    )
    # задачи держим в словаре по имени: метрика background_tasks должна называть
    # умершую задачу, а не сообщать «одна из пяти»
    if backup is not None and backup.enabled:
        background["backup"] = asyncio.create_task(backup.run_forever())
    background["outbox-dispatcher"] = asyncio.create_task(outbox_dispatcher.run_forever())
    # межпроцессная инвалидация кэша настроек (Postgres LISTEN/NOTIFY); на SQLite
    # листенера нет (None), там бот — единственный писатель
    if container.settings_listener is not None:
        settings_listener = cast(SettingsChangeListener, container.settings_listener)
        background["settings-listener"] = asyncio.create_task(settings_listener.run_forever())
        listeners["settings"] = settings_listener
    # межпроцессная инвалидация кэша тарифов (панель выдала/сняла подписку → бот перечитал)
    if container.entitlements_listener is not None:
        entitlements_listener = cast(EntitlementChangeListener, container.entitlements_listener)
        background["entitlements-listener"] = asyncio.create_task(
            entitlements_listener.run_forever()
        )
        listeners["entitlements"] = entitlements_listener
    # межпроцессная инвалидация кэша персон (панель изменила персону → бот перечитал)
    if container.persona_listener is not None:
        persona_listener = cast(PersonaChangeListener, container.persona_listener)
        background["persona-listener"] = asyncio.create_task(persona_listener.run_forever())
        listeners["persona"] = persona_listener
    # командный мост (Postgres): исполняет команды панели в Discord
    if command_listener is not None:
        background["command-listener"] = asyncio.create_task(command_listener.run_forever())
        listeners["command"] = command_listener
    started = sorted(background)
    boot.info("Фоновые задачи запущены: %s", ", ".join(started))

    # docker stop / kill шлёт SIGTERM — по умолчанию Python не переводит его в
    # чистую остановку, поэтому вешаем graceful bot.close() (он сольёт буферы
    # активности). На Windows-деве add_signal_handler недоступен — молча пропускаем.
    import signal

    def _graceful_stop() -> None:
        boot.info("Получен SIGTERM — плавно останавливаюсь")
        asyncio.create_task(bot.close())

    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, _graceful_stop)
    except (NotImplementedError, AttributeError):
        pass  # Windows / платформа без сигналов

    try:
        # async with гарантирует bot.close() (и отключение голосовых клиентов)
        # при любом завершении, включая Ctrl+C
        async with bot:
            await bot.start(settings.discord_token)
    except discord.LoginFailure:
        logger.error("Не удалось войти в Discord: неверный DISCORD_TOKEN")
        raise
    except (discord.HTTPException, ConnectionError, OSError) as exc:
        logger.error("Сеть Discord недоступна при запуске: %s", exc)
        raise
    finally:
        boot.info("Останавливаю фоновые задачи (%d), закрываю AI и БД…", len(background))
        for task in background.values():
            task.cancel()
        await health_runner.cleanup()
        if container.ai_provider is not None:
            await cast(IAIProvider, container.ai_provider).close()
        if container.chime_provider is not None:
            await cast(IAIProvider, container.chime_provider).close()
        from src.infrastructure.render.browser import CardRenderer

        await cast(CardRenderer, container.card_renderer).close()
        await engine.dispose()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")


if __name__ == "__main__":
    main()
