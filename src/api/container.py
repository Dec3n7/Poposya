"""Лёгкая DI-сборка для API-процесса.

В отличие от `build_root_container` бота НЕ поднимает Discord-клиент, аудио-
источник, AI-провайдеров и фоновые циклы — API они не нужны. Держит только то,
что требуется REST-слою: движок БД, фабрику сессий, сервисы и read-use-case'ы
поверх них (те же классы, что у бота — бизнес-логика не дублируется).
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.api.bot_guilds import BotGuildsCache
from src.application.activity.use_cases import VoiceLeaderboardUseCase
from src.application.appeals.use_cases import ListPendingAppealsUseCase
from src.application.audit.use_cases import AppendAuditUseCase, ListAuditUseCase
from src.application.banwatch.use_cases import CheckUserUseCase, FlaggedCandidatesUseCase
from src.application.botprofile.use_cases import GetBotProfileUseCase, SetBotProfileUseCase
from src.application.cinema.use_cases import (
    GetMovieRatingsUseCase,
    ListWatchlistUseCase,
    RemoveMovieUseCase,
    TopWatchedUseCase,
)
from src.application.finds.use_cases import GetActiveFindUseCase, TopCollectorsUseCase
from src.application.interfaces.unit_of_work import IUnitOfWork
from src.application.message_activity.use_cases import GetActivityStatsUseCase
from src.application.metrics.use_cases import GetTrendsUseCase
from src.application.moderation.use_cases import (
    ClearWarnsUseCase,
    GetUserHistoryUseCase,
    GetWarnsUseCase,
    ListGuildWarnsUseCase,
    ListTempBansUseCase,
)
from src.application.music.use_cases import (
    DeletePlaylistUseCase,
    ListPlaylistsUseCase,
    LoadPlaylistUseCase,
)
from src.application.player.use_cases import GetPlayerStateUseCase
from src.application.relationship.use_cases import (
    GetLeaderboardUseCase,
    GetRankUseCase,
    ListProfilesUseCase,
    SetPointsUseCase,
    ToggleFreezeUseCase,
    UpcomingBirthdaysUseCase,
)
from src.application.roles.use_cases import (
    DeleteRoleTemplateUseCase,
    GetRoleTemplateUseCase,
    ListRolesUseCase,
    ListRoleTemplatesUseCase,
    MemberRolesUseCase,
    SaveRoleTemplateUseCase,
)
from src.config import Settings
from src.domain.relationship.policies import PointsToLevelPolicy
from src.infrastructure.db.session import create_engine, create_session_factory
from src.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from src.infrastructure.events.in_memory_bus import InMemoryEventBus
from src.infrastructure.guild_settings import GuildSettingsService
from src.infrastructure.persona_listener import make_persona_listener
from src.infrastructure.persona_service import PersonaService
from src.infrastructure.settings_listener import make_settings_listener


@dataclass
class ApiContainer:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    guild_settings: GuildSettingsService
    # персоны (текст/личность бота): API держит свой кэш + слушает NOTIFY, как
    # и с настройками — правки из панели должны быть видны сразу
    persona: PersonaService
    # серверы, где есть бот (проверка «есть что настраивать») — кэш поверх Discord
    bot_guilds: BotGuildsCache
    # read-use-case'ы для дашборда (те же классы, что у бота)
    leaderboard: GetLeaderboardUseCase
    list_watchlist: ListWatchlistUseCase
    top_watched: TopWatchedUseCase
    movie_ratings: GetMovieRatingsUseCase
    remove_movie: RemoveMovieUseCase
    list_playlists: ListPlaylistsUseCase
    load_playlist: LoadPlaylistUseCase
    delete_playlist: DeletePlaylistUseCase
    now_playing: GetPlayerStateUseCase
    # витрины «Обзора»: топ по войсу + ближайшие ДР
    voice_leaderboard: VoiceLeaderboardUseCase
    upcoming_birthdays: UpcomingBirthdaysUseCase
    # люди/отношения: карточка человека + admin-действия
    get_rank: GetRankUseCase
    set_points: SetPointsUseCase
    toggle_freeze: ToggleFreezeUseCase
    list_profiles: ListProfilesUseCase
    # модерация (только чтение + безопасный сброс варнов — без Discord-побочки)
    get_warns: GetWarnsUseCase
    guild_warns: ListGuildWarnsUseCase
    clear_warns: ClearWarnsUseCase
    list_bans: ListTempBansUseCase
    user_history: GetUserHistoryUseCase  # единый журнал действий по участнику
    list_pending_appeals: ListPendingAppealsUseCase
    # находки: активная находка + топ коллекционеров сервера
    active_find: GetActiveFindUseCase
    top_collectors: TopCollectorsUseCase
    # тренды дашборда: серии суточных снапшотов метрик
    get_trends: GetTrendsUseCase
    # активность сервера: сообщения/день + хитмап день-недели×час
    activity_stats: GetActivityStatsUseCase
    # журнал действий панели
    append_audit: AppendAuditUseCase
    list_audit: ListAuditUseCase
    # кросс-серверные баны (показ модератору в панели)
    banwatch_check: CheckUserUseCase
    banwatch_flagged: FlaggedCandidatesUseCase
    # пер-серверный профиль бота (ник/аватар/баннер)
    get_bot_profile: GetBotProfileUseCase
    set_bot_profile: SetBotProfileUseCase
    # роли: зеркало, которое бот держит актуальным; выдача/снятие идёт через мост
    list_roles: ListRolesUseCase
    member_roles: MemberRolesUseCase
    # сохранённые в панели шаблоны ролей (применение — через мост role.import)
    save_role_template: SaveRoleTemplateUseCase
    list_role_templates: ListRoleTemplatesUseCase
    get_role_template: GetRoleTemplateUseCase
    delete_role_template: DeleteRoleTemplateUseCase
    # Тот же слушатель Postgres NOTIFY, что у бота: API тоже кэширует настройки,
    # и запись из бота/другого инстанса должна инвалидировать этот кэш. На SQLite
    # (dev без панели) фабрика вернёт None.
    settings_listener: object | None
    # аналогичный слушатель для персон (Postgres NOTIFY; None на SQLite)
    persona_listener: object | None


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

    persona = PersonaService(settings, session_factory)
    persona_listener = make_persona_listener(settings.database_url, persona)

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
        persona=persona,
        bot_guilds=BotGuildsCache(settings.discord_token),
        leaderboard=GetLeaderboardUseCase(uow_factory, policy, settings_provider=guild_settings),
        list_watchlist=ListWatchlistUseCase(uow_factory),
        top_watched=TopWatchedUseCase(uow_factory),
        movie_ratings=GetMovieRatingsUseCase(uow_factory),
        remove_movie=RemoveMovieUseCase(uow_factory),
        list_playlists=ListPlaylistsUseCase(uow_factory),
        load_playlist=LoadPlaylistUseCase(uow_factory),
        delete_playlist=DeletePlaylistUseCase(uow_factory),
        now_playing=GetPlayerStateUseCase(uow_factory),
        voice_leaderboard=VoiceLeaderboardUseCase(uow_factory),
        upcoming_birthdays=UpcomingBirthdaysUseCase(uow_factory),
        get_rank=GetRankUseCase(uow_factory, policy, settings_provider=guild_settings),
        set_points=SetPointsUseCase(uow_factory, policy, settings_provider=guild_settings),
        toggle_freeze=ToggleFreezeUseCase(uow_factory),
        list_profiles=ListProfilesUseCase(uow_factory, policy, settings_provider=guild_settings),
        get_warns=GetWarnsUseCase(uow_factory),
        guild_warns=ListGuildWarnsUseCase(uow_factory),
        clear_warns=ClearWarnsUseCase(uow_factory),
        list_bans=ListTempBansUseCase(uow_factory),
        user_history=GetUserHistoryUseCase(uow_factory),
        list_pending_appeals=ListPendingAppealsUseCase(uow_factory),
        active_find=GetActiveFindUseCase(uow_factory),
        top_collectors=TopCollectorsUseCase(uow_factory),
        get_trends=GetTrendsUseCase(uow_factory),
        activity_stats=GetActivityStatsUseCase(uow_factory),
        append_audit=AppendAuditUseCase(uow_factory),
        list_audit=ListAuditUseCase(uow_factory),
        banwatch_check=CheckUserUseCase(uow_factory),
        banwatch_flagged=FlaggedCandidatesUseCase(uow_factory),
        get_bot_profile=GetBotProfileUseCase(uow_factory),
        set_bot_profile=SetBotProfileUseCase(uow_factory),
        list_roles=ListRolesUseCase(uow_factory),
        member_roles=MemberRolesUseCase(uow_factory),
        save_role_template=SaveRoleTemplateUseCase(uow_factory),
        list_role_templates=ListRoleTemplatesUseCase(uow_factory),
        get_role_template=GetRoleTemplateUseCase(uow_factory),
        delete_role_template=DeleteRoleTemplateUseCase(uow_factory),
        settings_listener=settings_listener,
        persona_listener=persona_listener,
    )
