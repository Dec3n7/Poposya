import asyncio
import logging

import discord
from sqlalchemy import text

from src.application.di.root_container import build_root_container
from src.config import Settings
from src.infrastructure.db.backup import make_backup_service
from src.infrastructure.diagnostics import log_boot_summary, probe_dependencies
from src.infrastructure.discord.client import PoposyaBot
from src.infrastructure.logging.json_formatter import setup_logging
from src.infrastructure.web.app import HealthChecker, start_health_server

logger = logging.getLogger(__name__)
boot = logging.getLogger("boot")


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")


def _database_check(engine):
    async def check() -> bool:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    return check


def _discord_check(bot: PoposyaBot):
    async def check() -> bool:
        return bot.is_ready()

    return check


async def run() -> None:
    settings = Settings()
    setup_logging(settings.log_level, settings.log_format, settings.log_file)
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
    # пер-гильдийные настройки (/config) — поднять переопределения в память
    await container.guild_settings.load_all()
    # персоны (текст/личность бота) — поднять в память (в проде сид «Попоси» уже
    # в БД из миграции 0031; иначе load_all создаст дефолт-строку идемпотентно)
    await container.persona.load_all()
    bot = PoposyaBot(container)

    # командный мост панель→бот: панель кладёт команду в bot_commands + NOTIFY,
    # бот исполняет реальное Discord-действие (бан/мут/музыка). Только Postgres.
    from src.infrastructure.commands.listener import make_command_listener
    from src.infrastructure.discord.command_executor import DiscordCommandExecutor

    command_executor = DiscordCommandExecutor(bot, container.moderation)
    command_listener = make_command_listener(
        settings.database_url, container.session_factory, command_executor.execute
    )

    health = HealthChecker()
    health.register("database", _database_check(container.engine))
    health.register("discord", _discord_check(bot))
    health_runner = await start_health_server(health, settings.health_port)

    background: list[asyncio.Task] = []
    backup = make_backup_service(
        settings.database_url,
        settings.backup_dir,
        settings.backup_interval_hours,
        settings.backup_keep,
    )
    started = []
    if backup is not None and backup.enabled:
        background.append(asyncio.create_task(backup.run_forever()))
        started.append("backup")
    background.append(asyncio.create_task(container.outbox_dispatcher.run_forever()))
    started.append("outbox-dispatcher")
    # межпроцессная инвалидация кэша настроек (Postgres LISTEN/NOTIFY); на SQLite
    # листенера нет (None), там бот — единственный писатель
    if container.settings_listener is not None:
        background.append(asyncio.create_task(container.settings_listener.run_forever()))
        started.append("settings-listener")
    # межпроцессная инвалидация кэша персон (панель изменила персону → бот перечитал)
    if container.persona_listener is not None:
        background.append(asyncio.create_task(container.persona_listener.run_forever()))
        started.append("persona-listener")
    # командный мост (Postgres): исполняет команды панели в Discord
    if command_listener is not None:
        background.append(asyncio.create_task(command_listener.run_forever()))
        started.append("command-listener")
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
        for task in background:
            task.cancel()
        await health_runner.cleanup()
        if container.ai_provider is not None:
            await container.ai_provider.close()
        if container.chime_provider is not None:
            await container.chime_provider.close()
        await container.engine.dispose()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")


if __name__ == "__main__":
    main()
