from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class RepeatMode(Enum):
    OFF = "off"
    ONE = "one"
    ALL = "all"


@dataclass(frozen=True)
class Playlist:
    guild_id: int
    name: str
    created_by: int
    tracks: list["Track"]


@dataclass(frozen=True)
class Track:
    video_id: str
    title: str
    url: str
    duration: int | None  # секунды; None — прямой эфир
    requested_by: int
    uploader: str | None = None
    thumbnail: str | None = None

    @property
    def is_live(self) -> bool:
        return self.duration is None


@dataclass(frozen=True)
class LikedTrack:
    """Лайкнутый трек пользователя (глобально, без привязки к гильдии).
    Хранит метаданные, а не прямые ссылки на поток: video_id может умереть —
    тогда трек оживает поиском по названию (ResolveLikedUseCase)."""

    user_id: int
    video_id: str
    title: str
    uploader: str | None
    duration: int | None
    liked_at: datetime
    id: int | None = None

    def to_track(self, requested_by: int) -> Track:
        return Track(
            video_id=self.video_id,
            title=self.title,
            url=f"https://www.youtube.com/watch?v={self.video_id}",
            duration=self.duration,
            requested_by=requested_by,
            uploader=self.uploader,
            thumbnail=(
                f"https://i.ytimg.com/vi/{self.video_id}/hqdefault.jpg" if self.video_id else None
            ),
        )
