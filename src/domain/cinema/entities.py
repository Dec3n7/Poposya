from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MovieEntry:
    """Фильм в киноклубе сервера. Жизненный цикл:
    listed (в вотчлисте, копит 👍/👎) -> rating (смотрели, идёт сбор оценок)
    -> watched (в золотом фонде со средним баллом и вердиктом Попоси)."""

    guild_id: int
    title: str
    added_by: int
    added_at: datetime
    tmdb_id: int | None = None
    year: int | None = None
    overview: str = ""
    poster_url: str = ""
    message_id: int = 0  # карточка с кнопками 👍/👎
    channel_id: int = 0  # канал карточки/оценок (для итогов после рестарта)
    status: str = "listed"  # listed | rating | watched
    rating_message_id: int = 0  # сообщение с кнопками оценок 1–10
    rating_ends_at: datetime | None = None
    avg_score: float | None = None
    ratings_count: int = 0
    poposya_score: int | None = None
    poposya_review: str = ""
    watched_at: datetime | None = None
    id: int | None = None


@dataclass
class MovieNight:
    """Киновечер: опрос по топу вотчлиста -> победитель -> просмотр.
    Сбор оценок живёт на MovieEntry (статус rating), поэтому статусы ночи:
    poll -> scheduled -> done | cancelled."""

    guild_id: int
    created_by: int
    scheduled_at: datetime
    poll_ends_at: datetime
    candidate_ids: list[int] = field(default_factory=list)
    status: str = "poll"
    channel_id: int = 0
    poll_message_id: int = 0
    winner_message_id: int = 0
    winner_entry_id: int | None = None
    id: int | None = None

    @property
    def is_active(self) -> bool:
        return self.status in ("poll", "scheduled")
