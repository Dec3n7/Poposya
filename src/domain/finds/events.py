from dataclasses import dataclass

from src.domain.events.base import DomainEvent


@dataclass(frozen=True, kw_only=True)
class FindClaimed(DomainEvent):
    """Пользователь успешно забрал находку."""

    event_type: str = "finds.claimed"
    guild_id: int = 0
    find_id: int = 0
    user_id: int = 0
    item_id: str = ""
    reward: int = 0


@dataclass(frozen=True, kw_only=True)
class FindFailed(DomainEvent):
    """Поход за находкой не удался."""

    event_type: str = "finds.failed"
    guild_id: int = 0
    find_id: int = 0
    user_id: int = 0
    penalty: int = 0


@dataclass(frozen=True, kw_only=True)
class ItemGifted(DomainEvent):
    """Пользователь подарил предмет Попосе."""

    event_type: str = "finds.gifted"
    guild_id: int = 0
    user_id: int = 0
    item_id: str = ""
    bonus: int = 0
