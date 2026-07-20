from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PlayerTrack:
    """Трек в снапшоте живого плеера (для витрины на панели)."""

    title: str
    url: str
    duration: int | None  # None — прямой эфир
    requested_by: int
    uploader: str | None = None
    thumbnail: str | None = None


@dataclass
class PlayerState:
    """Снапшот живого плеера гильдии — поток бот→панель. Позиция хранится как
    (position_seconds на момент position_at); панель сама тикает прогресс между
    опросами: elapsed = is_paused ? position : position + (now - position_at)."""

    guild_id: int
    is_active: bool
    current: PlayerTrack | None = None
    queue: list[PlayerTrack] = field(default_factory=list)
    position_seconds: int = 0
    is_paused: bool = False
    repeat: str = "off"
    volume: float = 1.0
    position_at: datetime | None = None
    updated_at: datetime | None = None
