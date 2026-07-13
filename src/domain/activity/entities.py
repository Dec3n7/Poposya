from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Reminder:
    user_id: int
    guild_id: int
    text: str
    due_at: datetime
    id: int | None = None
