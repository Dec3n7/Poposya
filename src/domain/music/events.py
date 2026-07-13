from dataclasses import dataclass

from src.domain.events.base import DomainEvent


@dataclass(frozen=True, kw_only=True)
class TrackStarted(DomainEvent):
    # DomainEvent, не Critical: уведомление для других фич (лог, статистика,
    # реплика персоны); потеря не ломает консистентность.
    event_type: str = "music.track_started"
    guild_id: int = 0
    channel_id: int = 0  # текстовый канал сообщения плеера (для реакций других фич)
    title: str = ""
    url: str = ""
    requested_by: int = 0
