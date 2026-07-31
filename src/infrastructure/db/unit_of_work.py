import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.events.base import CriticalDomainEvent, DomainEvent
from src.domain.events.bus import IEventBus
from src.infrastructure.db.repositories.activity import (
    SqlAlchemyAlbumRepository,
    SqlAlchemyMemberActivityRepository,
    SqlAlchemyReminderRepository,
    SqlAlchemyVoiceProgressRepository,
)
from src.infrastructure.db.repositories.audit import SqlAlchemyAuditRepository
from src.infrastructure.db.repositories.banwatch import SqlAlchemyServerBanRepository
from src.infrastructure.db.repositories.botprofile import SqlAlchemyBotProfileRepository
from src.infrastructure.db.repositories.cinema import (
    SqlAlchemyMovieEntryRepository,
    SqlAlchemyMovieNightRepository,
    SqlAlchemyMovieRatingRepository,
)
from src.infrastructure.db.repositories.finds import (
    SqlAlchemyCollectionRepository,
    SqlAlchemyFindAttemptRepository,
    SqlAlchemyNightFindRepository,
)
from src.infrastructure.db.repositories.message_activity import (
    SqlAlchemyMessageActivityRepository,
)
from src.infrastructure.db.repositories.metrics import SqlAlchemyMetricsRepository
from src.infrastructure.db.repositories.moderation import (
    SqlAlchemyTempBanRepository,
    SqlAlchemyWarnRepository,
)
from src.infrastructure.db.repositories.music import (
    SqlAlchemyLikedTrackRepository,
    SqlAlchemyPlaylistRepository,
)
from src.infrastructure.db.repositories.player import SqlAlchemyPlayerStateRepository
from src.infrastructure.db.repositories.relationship import (
    SqlAlchemyDialogSummaryRepository,
    SqlAlchemyRelationshipRepository,
    SqlAlchemySecretRoomRepository,
)
from src.infrastructure.db.repositories.repos import SqlAlchemyTrackedRepoRepository
from src.infrastructure.db.repositories.roles import SqlAlchemyRoleRepository
from src.infrastructure.db.repositories.staykick import SqlAlchemyPendingKickRepository
from src.infrastructure.db.repositories.steam import SqlAlchemyTrackedGameRepository
from src.infrastructure.db.repositories.tempvoice import SqlAlchemyTempVoiceRepository
from src.infrastructure.events.outbox import outbox_row_for

logger = logging.getLogger(__name__)


class SqlAlchemyUnitOfWork(IUnitOfWork):
    """События публикуются строго ПОСЛЕ успешного commit (ТЗ 6.4); при
    откате транзакции накопленные события отбрасываются.

    CriticalDomainEvent дополнительно пишется в outbox_events В ТОЙ ЖЕ
    транзакции, что и данные: если процесс упадёт между commit и публикацией,
    OutboxDispatcher доставит событие после рестарта (at-least-once)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], event_bus: IEventBus):
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._session: AsyncSession | None = None
        self._events: list[DomainEvent] = []
        self._committed = False

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        self._session = self._session_factory()
        self.relationships = SqlAlchemyRelationshipRepository(self._session)
        self.secret_rooms = SqlAlchemySecretRoomRepository(self._session)
        self.dialog_summaries = SqlAlchemyDialogSummaryRepository(self._session)
        self.warns = SqlAlchemyWarnRepository(self._session)
        self.temp_bans = SqlAlchemyTempBanRepository(self._session)
        self.member_activity = SqlAlchemyMemberActivityRepository(self._session)
        self.reminders = SqlAlchemyReminderRepository(self._session)
        self.voice_progress = SqlAlchemyVoiceProgressRepository(self._session)
        self.album_posts = SqlAlchemyAlbumRepository(self._session)
        self.playlists = SqlAlchemyPlaylistRepository(self._session)
        self.liked_tracks = SqlAlchemyLikedTrackRepository(self._session)
        self.night_finds = SqlAlchemyNightFindRepository(self._session)
        self.collections = SqlAlchemyCollectionRepository(self._session)
        self.find_attempts = SqlAlchemyFindAttemptRepository(self._session)
        self.movies = SqlAlchemyMovieEntryRepository(self._session)
        self.movie_nights = SqlAlchemyMovieNightRepository(self._session)
        self.movie_ratings = SqlAlchemyMovieRatingRepository(self._session)
        self.pending_kicks = SqlAlchemyPendingKickRepository(self._session)
        self.temp_voice = SqlAlchemyTempVoiceRepository(self._session)
        self.metrics = SqlAlchemyMetricsRepository(self._session)
        self.message_activity = SqlAlchemyMessageActivityRepository(self._session)
        self.audit = SqlAlchemyAuditRepository(self._session)
        self.player_state = SqlAlchemyPlayerStateRepository(self._session)
        self.bot_profile = SqlAlchemyBotProfileRepository(self._session)
        self.roles = SqlAlchemyRoleRepository(self._session)
        self.tracked_repos = SqlAlchemyTrackedRepoRepository(self._session)
        self.tracked_games = SqlAlchemyTrackedGameRepository(self._session)
        self.server_bans = SqlAlchemyServerBanRepository(self._session)
        self._events = []
        self._committed = False
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        assert self._session is not None
        if not self._committed:
            await self._session.rollback()
        await self._session.close()

    def add_event(self, event: DomainEvent) -> None:
        self._events.append(event)

    async def commit(self) -> None:
        assert self._session is not None
        # критичные события — в outbox той же транзакцией, что и данные
        outbox_rows = [
            (outbox_row_for(event), event)
            for event in self._events
            if isinstance(event, CriticalDomainEvent)
        ]
        for row, _ in outbox_rows:
            self._session.add(row)
        await self._session.commit()
        self._committed = True
        events, self._events = self._events, []
        published_ids = set()
        for event in events:
            try:
                await self._event_bus.publish(event)
                published_ids.add(event.event_id)
            except Exception:
                # шина сама изолирует ошибки подписчиков; сюда попадают
                # только сбои самой публикации — транзакцию они не откатывают;
                # критичное событие останется в outbox и будет доставлено позже
                logger.exception("Публикация события упала", extra={"event_type": event.event_type})
        if outbox_rows:
            now = datetime.now(UTC).replace(tzinfo=None)
            marked = False
            for row, event in outbox_rows:
                if event.event_id in published_ids:
                    row.published_at = now
                    marked = True
            if marked:
                await self._session.commit()

    async def rollback(self) -> None:
        assert self._session is not None
        await self._session.rollback()
        self._events = []
