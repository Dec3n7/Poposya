"""Порт тарифов (entitlement) сервера.

Подготовка к монетизации: слой, отвечающий на вопрос «какой тариф у гильдии».
Сегодня реализация — заглушка (у всех максимальный тариф), поэтому ни кламп
лимитов, ни гейт фич по тарифу ничего не меняют. См.
docs/plans/monetization-prep.md (Prep 4)."""

from abc import ABC, abstractmethod
from enum import IntEnum


class PlanTier(IntEnum):
    """Тариф сервера. IntEnum — чтобы работало сравнение уровней:
    `tier >= PlanTier.PREMIUM`. Значения возрастают по «мощности» тарифа."""

    FREE = 0
    PREMIUM = 1
    PRO = 2


class IEntitlements(ABC):
    """Синхронный (горячий путь): реализация держит тарифы в памяти, как и
    провайдер настроек. Возвращает текущий тариф гильдии."""

    @abstractmethod
    def tier(self, guild_id: int) -> PlanTier: ...
