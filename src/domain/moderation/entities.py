from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Warn:
    guild_id: int
    user_id: int
    moderator_id: int
    reason: str
    created_at: datetime
    id: int | None = None


@dataclass(frozen=True)
class TempBan:
    guild_id: int
    user_id: int
    moderator_id: int
    reason: str
    expires_at: datetime
    id: int | None = None
