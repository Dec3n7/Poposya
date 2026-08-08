"""Сущности достижений — чистый домен, без фреймворков и БД.

Ачивка описывается предикатом над `UserStats` (снимок показателей участника) —
поэтому разблокировка **выводима из текущего состояния**, а не копится счётчиками.
Это даёт бесплатный бэкфилл (посчитал стату — открыл всё заслуженное) и делает
каталог тривиально тестируемым: предикат + число для карточки, оба — функции.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Tier(StrEnum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    LEGENDARY = "legendary"


@dataclass(frozen=True)
class UserStats:
    """Снимок показателей участника на сервере — единственный вход для условий.

    Собирается из существующих репозиториев (профиль отношений, коллекция
    находок, лайки, войс-минуты), новых счётчиков не заводит."""

    points: int = 0
    level: int = 0  # сколько порогов ролей пройдено
    is_exclusive: bool = False
    deep_dialogs: int = 0
    survey_completed: bool = False
    finds_count: int = 0
    has_legendary_find: bool = False
    likes_count: int = 0
    voice_hours: float = 0.0


@dataclass(frozen=True)
class Achievement:
    """Запись каталога. `stat` — число для крупной подписи на карточке,
    `unlocked` — условие открытия. Обе считаются от `UserStats`."""

    id: str
    name: str
    description: str
    tier: Tier
    icon: str  # ключ иконки в рендере карточки (ICONS в render/cards.py)
    stat_label: str
    stat: Callable[[UserStats], int]
    unlocked: Callable[[UserStats], bool]


@dataclass(frozen=True)
class UnlockedAchievement:
    user_id: int
    guild_id: int
    achievement_id: str
    unlocked_at: datetime


def newly_unlocked(
    stats: UserStats,
    catalog: list[Achievement],
    already_unlocked: set[str],
) -> list[Achievement]:
    """Ачивки, которые участник заслужил, но которых у него ещё нет.

    Чистая функция — сердце фичи: событие/бэкфилл/`/achievements` зовут её
    одинаково, разница только в источнике `stats`."""
    return [a for a in catalog if a.id not in already_unlocked and a.unlocked(stats)]
