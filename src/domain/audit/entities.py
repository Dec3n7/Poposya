from dataclasses import dataclass
from datetime import datetime


@dataclass
class AuditEntry:
    """Одно действие, совершённое через веб-панель: кто (actor), что (action),
    над кем/чем (target), детали и результат. Пишется для всех write-действий —
    и прошедших через мост (бан/мут/музыка), и прямых-в-БД (очки/настройки/…)."""

    guild_id: int
    actor_id: int
    action: str
    target: str | None = None
    details: str | None = None  # компактный JSON с параметрами
    result: str | None = None
    created_at: datetime | None = None
    id: int | None = None
