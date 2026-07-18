"""Лёгкая DI-сборка для API-процесса.

В отличие от `build_root_container` бота НЕ поднимает Discord-клиент, аудио-
источник, AI-провайдеров и фоновые циклы — API они не нужны. Держит только то,
что требуется REST-слою: движок БД, фабрику сессий, сервисы и read-use-case'ы
поверх них (те же классы, что у бота — бизнес-логика не дублируется).
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.api.bot_guilds import BotGuildsCache
from src.application.cinema.use_cases import ListWatchlistUseCase, TopWatchedUseCase
from src.application.interfaces.unit_of_work import IUnitOfWork
from src.application.moderation.use_cases import (
    ClearWarnsUseCase,
    GetWarnsUseCase,
    ListTempBansUseCase,
)
from src.application.music.use_cases import ListPlaylistsUseCase
from src.application.relationship.use_cases import (
    GetLeaderboardUseCase,
    GetRankUseCase,
    SetPointsUseCase,
    ToggleFreezeUseCase,
)
from src.config import Settings
from src.domain.relationship.policies import PointsToLevelPolicy
from src.infrastructure.db.session import create_engine, create_session_factory
from src.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from src.infrastructure.events.in_memory_bus import InMemoryEventBus
from src.infrastructure.guild_settings import GuildSettingsService
from src.infrastructure.settings_listener import make_settings_listener


@dataclass
class ApiContainer:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    guild_settings: GuildSettingsService
    # серверы, где есть бот (проверка «есть что настраивать») — кэш поверх Discord
    bot_guilds: BotGuildsCache
    # read-use-case'ы для дашборда (те же классы, что у бота)
    leaderboard: GetLeaderboardUseCase
    list_watchlist: ListWatchlistUseCase
    top_watched: TopWatchedUseCase
    list_playlists: ListPlaylistsUseCase
    # люди/отношения: карточка человека + admin-действия
    get_rank: GetRankUseCase
    set_points: SetPointsUseCase
    toggle_freeze: ToggleFreezeUseCase
    # модерация (только чтение + безопасный сброс варнов — без Discord-побочки)
    get_warns: GetWarnsUseCase
    clear_warns: ClearWarnsUseCase
    list_bans: ListTempBansUseCase
    # Тот же слушатель Postgres NOTIFY, что у бота: API тоже кэширует настройки,
    # и запись из бота/другого инстанса должна инвалидировать этот кэш. На SQLite
    # (dev без панели) фабрика вернёт None.
    settings_listener: object | None


def build_api_container(settings: Settings) -> ApiContainer:
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    return assemble_container(settings, engine, session_factory)


def assemble_container(
    settings: Settings,
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> ApiContainer:
    """Сборка контейнера поверх готовой фабрики сессий. Прод передаёт свежий
    движок; тесты — фабрику на schema'нутой тестовой БД (единая точка сборки,
    чтобы тесты не отставали при добавлении полей)."""
    guild_settings = GuildSettingsService(settings, session_factory)
    settings_listener = make_settings_listener(settings.database_url, guild_settings)

    event_bus = InMemoryEventBus()

    def uow_factory() -> IUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory, event_bus)

    policy = PointsToLevelPolicy(
        thresholds=tuple(settings.relationship_role_thresholds),
        exclusive_threshold=settings.relationship_exclusive_threshold,
    )

    return ApiContainer(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        guild_settings=guild_settings,
        bot_guilds=BotGuildsCache(settings.discord_token),
        leaderboard=GetLeaderboardUseCase(uow_factory, policy, settings_provider=guild_settings),
        list_watchlist=ListWatchlistUseCase(uow_factory),
        top_watched=TopWatchedUseCase(uow_factory),
        list_playlists=ListPlaylistsUseCase(uow_factory),
        get_rank=GetRankUseCase(uow_factory, policy, settings_provider=guild_settings),
        set_points=SetPointsUseCase(uow_factory, policy, settings_provider=guild_settings),
        toggle_freeze=ToggleFreezeUseCase(uow_factory),
        get_warns=GetWarnsUseCase(uow_factory),
        clear_warns=ClearWarnsUseCase(uow_factory),
        list_bans=ListTempBansUseCase(uow_factory),
        settings_listener=settings_listener,
    )
