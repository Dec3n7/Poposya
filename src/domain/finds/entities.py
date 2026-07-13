from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Rarity(StrEnum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    LEGENDARY = "legendary"


@dataclass
class NightFind:
    """Находка с ночной прогулки: анонсирована в канале, ждёт смельчака.
    Забирает первый, у кого получилось; после claimed_by или expires_at
    находка мертва."""

    guild_id: int
    location_id: str
    item_id: str
    created_at: datetime
    expires_at: datetime
    channel_id: int = 0
    message_id: int = 0
    claimed_by: int | None = None
    claimed_at: datetime | None = None
    id: int | None = None

    def is_active(self, now: datetime) -> bool:
        return self.claimed_by is None and now < self.expires_at


@dataclass(frozen=True)
class CollectionItem:
    """Предмет в коллекции пользователя; после дарения Попосе остаётся
    в истории с отметкой gifted_at."""

    guild_id: int
    user_id: int
    item_id: str
    obtained_at: datetime
    gifted_at: datetime | None = None
    id: int | None = None


@dataclass(frozen=True)
class FindAttempt:
    """Попытка похода (kind="claim") или специальная прогулка (kind="walk");
    по последней попытке считается кулдаун."""

    guild_id: int
    user_id: int
    kind: str  # claim | walk
    success: bool
    attempted_at: datetime
    find_id: int | None = None  # у walk находки нет
    id: int | None = None
