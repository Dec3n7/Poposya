from abc import ABC, abstractmethod

from src.domain.activity.repository import (
    IAlbumRepository,
    IMemberActivityRepository,
    IReminderRepository,
    IVoiceProgressRepository,
)
from src.domain.audit.repository import IAuditRepository
from src.domain.banwatch.repository import IServerBanRepository
from src.domain.botprofile.repository import IBotProfileRepository
from src.domain.cinema.repository import (
    IMovieEntryRepository,
    IMovieNightRepository,
    IMovieRatingRepository,
)
from src.domain.events.base import DomainEvent
from src.domain.finds.repository import (
    ICollectionRepository,
    IFindAttemptRepository,
    INightFindRepository,
)
from src.domain.message_activity.repository import IMessageActivityRepository
from src.domain.metrics.repository import IMetricsRepository
from src.domain.moderation.repository import ITempBanRepository, IWarnRepository
from src.domain.music.repository import ILikedTrackRepository, IPlaylistRepository
from src.domain.player.repository import IPlayerStateRepository
from src.domain.relationship.repository import (
    IDialogSummaryRepository,
    IRelationshipRepository,
    ISecretRoomRepository,
)
from src.domain.repos.repository import ITrackedRepoRepository
from src.domain.roles.repository import IRoleRepository
from src.domain.staykick.repository import IPendingKickRepository
from src.domain.steam.repository import ITrackedGameRepository
from src.domain.tempvoice.repository import ITempVoiceRepository


class IUnitOfWork(ABC):
    """Транзакция use case. События, добавленные через add_event, публикуются
    в шину только после успешного commit() (ТЗ 6.4). CriticalDomainEvent
    дополнительно проходит через Outbox: запись в той же транзакции,
    недоставленное добивает фоновый диспетчер (at-least-once)."""

    relationships: IRelationshipRepository
    secret_rooms: ISecretRoomRepository
    dialog_summaries: IDialogSummaryRepository
    warns: IWarnRepository
    temp_bans: ITempBanRepository
    member_activity: IMemberActivityRepository
    reminders: IReminderRepository
    voice_progress: IVoiceProgressRepository
    album_posts: IAlbumRepository
    playlists: IPlaylistRepository
    liked_tracks: ILikedTrackRepository
    night_finds: INightFindRepository
    collections: ICollectionRepository
    find_attempts: IFindAttemptRepository
    movies: IMovieEntryRepository
    movie_nights: IMovieNightRepository
    movie_ratings: IMovieRatingRepository
    pending_kicks: IPendingKickRepository
    temp_voice: ITempVoiceRepository
    metrics: IMetricsRepository
    message_activity: IMessageActivityRepository
    audit: IAuditRepository
    player_state: IPlayerStateRepository
    bot_profile: IBotProfileRepository
    roles: IRoleRepository
    tracked_repos: ITrackedRepoRepository
    tracked_games: ITrackedGameRepository
    server_bans: IServerBanRepository

    @abstractmethod
    async def __aenter__(self) -> "IUnitOfWork": ...

    @abstractmethod
    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    @abstractmethod
    async def commit(self) -> None: ...

    @abstractmethod
    async def rollback(self) -> None: ...

    @abstractmethod
    def add_event(self, event: DomainEvent) -> None: ...
