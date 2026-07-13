from dataclasses import dataclass

from src.domain.events.base import CriticalDomainEvent


@dataclass(frozen=True, kw_only=True)
class RelationshipRoleChanged(CriticalDomainEvent):
    # Critical: смена роли — «событие с очками», его потеря оставляет
    # Discord-роль рассинхронизированной до следующего начисления. UoW пишет
    # его в outbox в той же транзакции; доставка at-least-once, подписчик
    # (role sync) идемпотентен — сверяет состояние, а не применяет дельту.
    event_type: str = "relationship.role_changed"
    guild_id: int = 0
    user_id: int = 0
    channel_id: int = 0
    old_role_index: int | None = None
    new_role_index: int | None = None
    points: int = 0


@dataclass(frozen=True, kw_only=True)
class ExclusiveTransferred(CriticalDomainEvent):
    event_type: str = "relationship.exclusive_transferred"
    guild_id: int = 0
    new_user_id: int = 0
    previous_user_id: int | None = None
    channel_id: int = 0
