import logging
from dataclasses import dataclass
from pathlib import Path

from src.application.activity.di import ActivityContainer
from src.application.ai_chat.di import AIChatContainer
from src.application.cinema.di import CinemaContainer
from src.application.finds.di import FindsContainer
from src.application.moderation.di import ModerationContainer
from src.application.music.di import MusicContainer
from src.application.relationship.di import RelationshipContainer
from src.application.roles.di import RolesContainer
from src.application.tempvoice.di import TempVoiceContainer
from src.config import Settings
from src.domain.events.bus import IEventBus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RootContainer:
    settings: Settings
    event_bus: IEventBus
    music: MusicContainer
    relationship: RelationshipContainer
    ai_chat: AIChatContainer
    moderation: ModerationContainer
    activity: ActivityContainer
    finds: FindsContainer
    cinema: CinemaContainer
    staykick: object  # StayKickContainer
    tempvoice: TempVoiceContainer
    roles: RolesContainer
    guild_settings: object  # GuildSettingsService; main вызывает load_all
    engine: object  # AsyncEngine; закрывается в main при завершении
    session_factory: object  # async_sessionmaker; командному мосту в main
    ai_provider: object | None  # IAIProvider; закрывается в main
    chime_provider: object | None  # IAIProvider решения (дешёвая модель); закрывается в main
    outbox_dispatcher: object  # OutboxDispatcher; цикл запускает main
    settings_listener: object | None  # SettingsChangeListener (Postgres); цикл запускает main


def build_root_container(settings: Settings) -> RootContainer:
    # Composition root — единственное место, где application-слой
    # знает о конкретных инфраструктурных реализациях.
    from src.application.activity.use_cases import (
        AddReminderUseCase,
        GetVoiceHoursUseCase,
        LoadVoiceProgressUseCase,
        PopDueRemindersUseCase,
        SaveVoiceProgressUseCase,
        TouchMemberActivityUseCase,
        TryMarkAlbumPostUseCase,
    )
    from src.application.ai_chat.service import AIQueue, ChatService
    from src.application.message_activity.use_cases import RecordMessageActivityUseCase
    from src.application.metrics.use_cases import RecordDailySnapshotUseCase
    from src.application.moderation.use_cases import (
        ClearWarnsUseCase,
        GetWarnsUseCase,
        ListTempBansUseCase,
        PopExpiredBansUseCase,
        RemoveTempBanUseCase,
        TempBanUserUseCase,
        WarnUserUseCase,
    )
    from src.application.relationship.use_cases import (
        AddDialogSummaryUseCase,
        AwardPointUseCase,
        BirthdayTickUseCase,
        CompleteSurveyUseCase,
        DecayPointsUseCase,
        GetLeaderboardUseCase,
        GetRankUseCase,
        GetSecretCodeUseCase,
        IssueSecretCodeUseCase,
        PopExpiredSecretRoomsUseCase,
        RecordDeepDialogUseCase,
        RegisterSecretRoomUseCase,
        SetBirthdayUseCase,
        SetPointsUseCase,
        SetSurveyChoiceUseCase,
        ToggleFreezeUseCase,
        ToggleSurveyInterestUseCase,
        UpdateUserNotesUseCase,
        ValidateSecretCodeUseCase,
    )
    from src.domain.ai_chat.prompt import PromptTemplate
    from src.domain.relationship.policies import PointsToLevelPolicy
    from src.infrastructure.ai.groq_provider import GroqAIProvider
    from src.infrastructure.ai.rate_limiter import InMemoryRateLimiter
    from src.infrastructure.audio.ytdlp_source import YtDlpAudioSource
    from src.infrastructure.db.session import create_engine, create_session_factory
    from src.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
    from src.infrastructure.events.in_memory_bus import InMemoryEventBus
    from src.infrastructure.events.outbox import OutboxDispatcher

    event_bus = InMemoryEventBus()

    # --- БД / UoW ---
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    outbox_dispatcher = OutboxDispatcher(
        session_factory,
        event_bus,
        interval_seconds=settings.outbox_dispatch_interval,
        max_attempts=settings.outbox_max_attempts,
    )

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory, event_bus)

    # пер-гильдийные настройки (/config): переопределяют дефолты из .env
    from src.infrastructure.guild_settings import GuildSettingsService
    from src.infrastructure.settings_listener import make_settings_listener

    guild_settings = GuildSettingsService(settings, session_factory)
    # межпроцессная инвалидация кэша (веб-панель ∥ бот): Postgres LISTEN/NOTIFY.
    # На SQLite вернёт None — там второго писателя нет.
    settings_listener = make_settings_listener(settings.database_url, guild_settings)

    # --- relationship ---
    from src.domain.shared.holidays import HolidayCalendar

    calendar = HolidayCalendar(settings.holidays)
    policy = PointsToLevelPolicy(
        thresholds=tuple(settings.relationship_role_thresholds),
        exclusive_threshold=settings.relationship_exclusive_threshold,
    )
    award_point = AwardPointUseCase(
        uow_factory,
        policy,
        daily_cap=settings.relationship_daily_point_cap,
        absence_days=settings.relationship_absence_days,
        calendar=calendar,
        holiday_multiplier=settings.holiday_points_multiplier,
        settings_provider=guild_settings,
    )
    get_rank = GetRankUseCase(uow_factory, policy, settings_provider=guild_settings)
    update_notes = UpdateUserNotesUseCase(
        uow_factory, settings.relationship_notes_max_chars, settings_provider=guild_settings
    )
    relationship = RelationshipContainer(
        policy=policy,
        award_point=award_point,
        get_rank=get_rank,
        set_points=SetPointsUseCase(uow_factory, policy, settings_provider=guild_settings),
        toggle_freeze=ToggleFreezeUseCase(uow_factory),
        update_notes=update_notes,
        set_survey_choice=SetSurveyChoiceUseCase(uow_factory),
        toggle_survey_interest=ToggleSurveyInterestUseCase(uow_factory),
        complete_survey=CompleteSurveyUseCase(
            uow_factory,
            policy,
            bonus=settings.survey_bonus_points,
            settings_provider=guild_settings,
        ),
        set_birthday=SetBirthdayUseCase(uow_factory),
        birthday_tick=BirthdayTickUseCase(uow_factory, remind_days=settings.birthday_remind_days),
        leaderboard=GetLeaderboardUseCase(uow_factory, policy, settings_provider=guild_settings),
        decay_points=DecayPointsUseCase(
            uow_factory,
            policy,
            after_days=settings.relationship_decay_after_days,
            every_days=settings.relationship_decay_every_days,
            amount=settings.relationship_decay_points,
            settings_provider=guild_settings,
        ),
        record_deep_dialog=RecordDeepDialogUseCase(uow_factory),
        add_dialog_summary=AddDialogSummaryUseCase(
            uow_factory, keep=settings.ai_dialog_summary_keep
        ),
        issue_secret_code=IssueSecretCodeUseCase(uow_factory),
        validate_secret_code=ValidateSecretCodeUseCase(uow_factory),
        register_secret_room=RegisterSecretRoomUseCase(
            uow_factory, hours=settings.secret_room_hours, settings_provider=guild_settings
        ),
        get_secret_code=GetSecretCodeUseCase(uow_factory),
        pop_expired_secret_rooms=PopExpiredSecretRoomsUseCase(uow_factory),
        role_names=settings.relationship_role_names,
    )

    # --- ai_chat (Groq) ---
    from src.application.interfaces.ai_provider import IAIProvider
    from src.infrastructure.ai.circuit_breaker import CircuitBreakerAIProvider
    from src.infrastructure.ai.resilient import FallbackAIProvider, ResilientAIProvider

    # тип по интерфейсу: переменную по очереди оборачивают retry -> fallback ->
    # circuit breaker, каждый снова IAIProvider
    ai_provider: IAIProvider | None = None
    chime_provider: IAIProvider | None = None
    chat_service = None
    if settings.groq_api_key:
        prompt_path = Path(settings.ai_prompt_path)
        template = PromptTemplate(prompt_path.read_text(encoding="utf-8"))

        def _groq(model: str) -> GroqAIProvider:
            return GroqAIProvider(
                api_key=settings.groq_api_key,
                model=model,
                temperature=settings.ai_temperature,
                max_tokens=settings.ai_max_tokens,
                timeout_seconds=settings.ai_request_timeout,
            )

        # Цепочка надёжности (ТЗ 8.2): retry -> fallback-модель -> circuit
        # breaker снаружи всех, чтобы размыкаться на сбоях всей цепочки
        ai_provider = ResilientAIProvider(
            _groq(settings.ai_model),
            attempts=settings.ai_retry_attempts,
            base_delay=settings.ai_retry_base_delay,
        )
        if settings.ai_fallback_model and settings.ai_fallback_model != settings.ai_model:
            ai_provider = FallbackAIProvider(
                ai_provider,
                ResilientAIProvider(
                    _groq(settings.ai_fallback_model),
                    attempts=2,
                    base_delay=settings.ai_retry_base_delay,
                ),
            )
        ai_provider = CircuitBreakerAIProvider(
            ai_provider,
            failure_threshold=settings.ai_cb_failure_threshold,
            timeout=settings.ai_cb_timeout_seconds,
        )
        # пассивное вклинивание: решение — на дешёвой быстрой модели (фолбэк-
        # модель), генерация — на основной; шаблон решения грузим отдельно
        chime_template = None
        chime_path = Path(settings.ai_chime_prompt_path)
        if chime_path.exists():
            chime_template = PromptTemplate(chime_path.read_text(encoding="utf-8"))
            decision_model = settings.ai_fallback_model or settings.ai_model
            chime_provider = ResilientAIProvider(
                _groq(decision_model), attempts=2, base_delay=settings.ai_retry_base_delay
            )
        chat_service = ChatService(
            provider=ai_provider,
            queue=AIQueue(settings.ai_max_concurrent),
            rate_limiter=InMemoryRateLimiter(),
            award_point=award_point,
            get_rank=get_rank,
            update_notes=update_notes,
            template=template,
            role_names=settings.relationship_role_names,
            rate_limits_by_level=settings.ai_rate_limits_by_level,
            notes_max_chars=settings.relationship_notes_max_chars,
            calendar=calendar,
            add_dialog_summary=relationship.add_dialog_summary,
            record_deep_dialog=relationship.record_deep_dialog,
            dialog_gap_minutes=settings.ai_dialog_gap_minutes,
            dialog_min_exchanges=settings.ai_dialog_min_exchanges,
            deep_dialog_exchanges=settings.ai_deep_dialog_exchanges,
            settings_provider=guild_settings,
            chime_template=chime_template,
            chime_provider=chime_provider,
        )
    else:
        logger.warning("GROQ_API_KEY не задан — общение Попоси (ai_chat) отключено")

    from src.application.music.use_cases import (
        DeletePlaylistUseCase,
        ListLikedUseCase,
        ListPlaylistsUseCase,
        LoadPlaylistUseCase,
        RemoveLikedUseCase,
        ResolveLikedUseCase,
        SavePlaylistUseCase,
        ToggleLikeUseCase,
    )
    from src.application.player.use_cases import SavePlayerStateUseCase
    from src.infrastructure.audio.cache import AudioCache

    audio_cache = (
        AudioCache(settings.music_cache_dir, settings.music_cache_max_mb * 1024 * 1024)
        if settings.music_prefetch_tracks > 0
        else None
    )
    audio_source = YtDlpAudioSource(
        cookies_from_browser=settings.ytdlp_cookies_from_browser,
        cookies_file=settings.ytdlp_cookies_file,
        cache=audio_cache,
    )
    music = MusicContainer(
        settings=settings,
        event_bus=event_bus,
        audio_source=audio_source,
        save_playlist=SavePlaylistUseCase(
            uow_factory,
            max_per_guild=settings.music_playlist_max_per_guild,
            max_tracks=settings.music_playlist_max_tracks,
        ),
        load_playlist=LoadPlaylistUseCase(uow_factory),
        list_playlists=ListPlaylistsUseCase(uow_factory),
        delete_playlist=DeletePlaylistUseCase(uow_factory),
        toggle_like=ToggleLikeUseCase(uow_factory, max_per_user=settings.music_liked_max_per_user),
        list_liked=ListLikedUseCase(uow_factory),
        remove_liked=RemoveLikedUseCase(uow_factory),
        resolve_liked=ResolveLikedUseCase(uow_factory, audio_source),
        save_player_state=SavePlayerStateUseCase(uow_factory),
    )

    moderation = ModerationContainer(
        warn_user=WarnUserUseCase(
            uow_factory,
            threshold=settings.warn_threshold,
            settings_provider=guild_settings,
        ),
        get_warns=GetWarnsUseCase(uow_factory),
        clear_warns=ClearWarnsUseCase(uow_factory),
        temp_ban=TempBanUserUseCase(uow_factory),
        remove_ban=RemoveTempBanUseCase(uow_factory),
        list_bans=ListTempBansUseCase(uow_factory),
        pop_expired_bans=PopExpiredBansUseCase(uow_factory),
    )
    activity = ActivityContainer(
        touch_activity=TouchMemberActivityUseCase(
            uow_factory,
            absent_days_threshold=settings.absent_days_threshold,
            settings_provider=guild_settings,
        ),
        add_reminder=AddReminderUseCase(uow_factory),
        pop_due_reminders=PopDueRemindersUseCase(uow_factory),
        try_mark_album=TryMarkAlbumPostUseCase(uow_factory),
        load_voice_progress=LoadVoiceProgressUseCase(uow_factory),
        save_voice_progress=SaveVoiceProgressUseCase(uow_factory),
        get_voice_hours=GetVoiceHoursUseCase(uow_factory),
        record_snapshot=RecordDailySnapshotUseCase(uow_factory),
        record_message_activity=RecordMessageActivityUseCase(uow_factory),
    )

    from src.application.finds.use_cases import (
        ClaimFindUseCase,
        GetActiveFindUseCase,
        GetCollectionUseCase,
        GiftItemUseCase,
        ListLiveFindsUseCase,
        RegisterFindMessageUseCase,
        SpawnFindUseCase,
        SpecialWalkUseCase,
    )

    finds = FindsContainer(
        spawn_find=SpawnFindUseCase(uow_factory, lifetime_hours=settings.finds_lifetime_hours),
        register_find_message=RegisterFindMessageUseCase(uow_factory),
        claim_find=ClaimFindUseCase(
            uow_factory,
            policy,
            cooldown_hours=settings.finds_claim_cooldown_hours,
            fail_penalty=settings.finds_fail_penalty,
            notes_max_chars=settings.relationship_notes_max_chars,
            settings_provider=guild_settings,
        ),
        gift_item=GiftItemUseCase(
            uow_factory, policy, notes_max_chars=settings.relationship_notes_max_chars
        ),
        get_collection=GetCollectionUseCase(uow_factory),
        special_walk=SpecialWalkUseCase(
            uow_factory,
            policy,
            cost=settings.finds_walk_cost,
            cooldown_days=settings.finds_walk_cooldown_days,
        ),
        get_active_find=GetActiveFindUseCase(uow_factory),
        list_live_finds=ListLiveFindsUseCase(uow_factory),
    )

    from src.application.cinema.use_cases import (
        AddMovieUseCase,
        CancelNightUseCase,
        CloseNightPollUseCase,
        FinalizeRatingUseCase,
        GetCinemaProfileUseCase,
        GetMovieRatingsUseCase,
        GetMovieReviewsUseCase,
        GetMovieUseCase,
        ListPendingCinemaUseCase,
        ListWatchlistUseCase,
        OpenRatingUseCase,
        RateMovieUseCase,
        RegisterMovieMessageUseCase,
        RemoveMovieUseCase,
        ReviewMovieUseCase,
        StartMovieNightUseCase,
        TopWatchedUseCase,
        VoteMovieUseCase,
        VoteNightUseCase,
    )

    cinema = CinemaContainer(
        add_movie=AddMovieUseCase(
            uow_factory,
            watchlist_max=settings.cinema_watchlist_max,
            settings_provider=guild_settings,
        ),
        register_message=RegisterMovieMessageUseCase(uow_factory),
        vote_movie=VoteMovieUseCase(uow_factory),
        list_watchlist=ListWatchlistUseCase(uow_factory),
        top_watched=TopWatchedUseCase(uow_factory),
        remove_movie=RemoveMovieUseCase(uow_factory),
        start_night=StartMovieNightUseCase(uow_factory, poll_options=settings.cinema_poll_options),
        vote_night=VoteNightUseCase(uow_factory),
        close_poll=CloseNightPollUseCase(uow_factory),
        cancel_night=CancelNightUseCase(uow_factory),
        open_rating=OpenRatingUseCase(
            uow_factory,
            rating_hours=settings.cinema_rating_hours,
            rating_minutes=settings.cinema_rating_minutes,
            settings_provider=guild_settings,
        ),
        rate_movie=RateMovieUseCase(uow_factory),
        review_movie=ReviewMovieUseCase(uow_factory),
        finalize_rating=FinalizeRatingUseCase(uow_factory),
        list_reviews=GetMovieReviewsUseCase(uow_factory),
        list_ratings=GetMovieRatingsUseCase(uow_factory),
        list_pending=ListPendingCinemaUseCase(uow_factory),
        get_movie=GetMovieUseCase(uow_factory),
        cinema_profile=GetCinemaProfileUseCase(uow_factory),
    )

    from src.application.staykick.di import StayKickContainer
    from src.application.staykick.use_cases import (
        CancelPendingKickUseCase,
        DueRemindersUseCase,
        PopDueKicksUseCase,
        SchedulePendingKickUseCase,
    )

    staykick = StayKickContainer(
        schedule_kick=SchedulePendingKickUseCase(uow_factory),
        cancel_kick=CancelPendingKickUseCase(uow_factory),
        pop_due_kicks=PopDueKicksUseCase(uow_factory),
        due_reminders=DueRemindersUseCase(uow_factory),
    )

    from src.application.tempvoice.use_cases import (
        ClaimTempChannelUseCase,
        CountTempChannelsUseCase,
        GetTempChannelUseCase,
        ListTempChannelsUseCase,
        RegisterTempChannelUseCase,
        ReleaseTempChannelUseCase,
    )

    tempvoice = TempVoiceContainer(
        register=RegisterTempChannelUseCase(uow_factory),
        release=ReleaseTempChannelUseCase(uow_factory),
        get=GetTempChannelUseCase(uow_factory),
        claim=ClaimTempChannelUseCase(uow_factory),
        count=CountTempChannelsUseCase(uow_factory),
        list_channels=ListTempChannelsUseCase(uow_factory),
    )

    from src.application.roles.use_cases import (
        DeleteRoleUseCase,
        ListRolesUseCase,
        MemberRolesUseCase,
        RemoveMemberUseCase,
        SetMemberRolesUseCase,
        SyncGuildRolesUseCase,
        SyncMembersUseCase,
        UpsertRoleUseCase,
    )

    roles = RolesContainer(
        sync_guild=SyncGuildRolesUseCase(uow_factory),
        upsert_role=UpsertRoleUseCase(uow_factory),
        delete_role=DeleteRoleUseCase(uow_factory),
        list_roles=ListRolesUseCase(uow_factory),
        sync_members=SyncMembersUseCase(uow_factory),
        set_member_roles=SetMemberRolesUseCase(uow_factory),
        remove_member=RemoveMemberUseCase(uow_factory),
        member_roles=MemberRolesUseCase(uow_factory),
    )

    return RootContainer(
        settings=settings,
        event_bus=event_bus,
        music=music,
        relationship=relationship,
        ai_chat=AIChatContainer(chat_service=chat_service),
        moderation=moderation,
        activity=activity,
        finds=finds,
        cinema=cinema,
        staykick=staykick,
        tempvoice=tempvoice,
        roles=roles,
        guild_settings=guild_settings,
        engine=engine,
        session_factory=session_factory,
        ai_provider=ai_provider,
        chime_provider=chime_provider,
        outbox_dispatcher=outbox_dispatcher,
        settings_listener=settings_listener,
    )
