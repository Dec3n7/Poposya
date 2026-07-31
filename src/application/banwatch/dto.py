from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class BanRecordDTO:
    """Один бан на одном сервере — для показа модератору."""

    guild_id: int
    guild_name: str
    reason: str
    banned_at: datetime | None


@dataclass(frozen=True)
class CrossBanReport:
    """Кросс-серверная бан-история пользователя (по ДРУГИМ серверам, не текущему).
    count — число серверов; records — от свежего к старому."""

    user_id: int
    count: int
    records: list[BanRecordDTO] = field(default_factory=list)


@dataclass(frozen=True)
class FlaggedUser:
    """Кандидат в «отмеченные»: забанен на count серверах (кроме текущего)."""

    user_id: int
    count: int
