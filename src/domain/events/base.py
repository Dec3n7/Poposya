from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    aggregate_id: str
    event_type: str
    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=_utcnow)
    version: int = 1


@dataclass(frozen=True, kw_only=True)
class CriticalDomainEvent(DomainEvent):
    """Наследоваться от этого класса, а не от DomainEvent, если потеря
    события недопустима для консистентности других фич (например,
    начисление/списание валюты, изменение прав доступа).
    UOW проверяет тип через isinstance и решает маршрут публикации
    (Outbox) автоматически — эта проверка не обходится вручную."""
