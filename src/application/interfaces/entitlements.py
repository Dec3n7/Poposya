"""Порт тарифов (entitlement) сервера.

Слой, отвечающий на вопрос «какой тариф у гильдии». Рабочая реализация —
`infrastructure.entitlements.EntitlementService` (БД + кэш): по ней клампятся
лимиты и гейтятся Premium-модули. См. docs/plans/monetization-prep.md."""

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
