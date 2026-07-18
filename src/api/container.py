"""Лёгкая DI-сборка для API-процесса.

В отличие от `build_root_container` бота НЕ поднимает Discord-клиент, аудио-
источник, AI-провайдеров и фоновые циклы — API они не нужны. Держит только то,
что требуется REST-слою: движок БД, фабрику сессий и сервисы поверх них.

По мере срезов сюда добавляются конкретные use-case'ы (тот же класс, что у
бота). Первый срез — только настройки (`GuildSettingsService`).
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.config import Settings
from src.infrastructure.db.session import create_engine, create_session_factory
from src.infrastructure.guild_settings import GuildSettingsService
from src.infrastructure.settings_listener import make_settings_listener


@dataclass
class ApiContainer:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    guild_settings: GuildSettingsService
    # Тот же слушатель Postgres NOTIFY, что у бота: API тоже кэширует настройки,
    # и запись из бота/другого инстанса должна инвалидировать этот кэш. На SQLite
    # (dev без панели) фабрика вернёт None.
    settings_listener: object | None


def build_api_container(settings: Settings) -> ApiContainer:
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    guild_settings = GuildSettingsService(settings, session_factory)
    settings_listener = make_settings_listener(settings.database_url, guild_settings)
    return ApiContainer(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        guild_settings=guild_settings,
        settings_listener=settings_listener,
    )
