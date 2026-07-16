from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PendingKick:
    """Запланированный авто-кик участника: напомнить в remind_at, выгнать в kick_at."""

    guild_id: int
    user_id: int
    remind_at: datetime
    kick_at: datetime
    reminded: bool = False
    created_at: datetime | None = None
