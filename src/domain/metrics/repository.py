from abc import ABC, abstractmethod
from datetime import date


class IMetricsRepository(ABC):
    """Суточные снапшоты числовых метрик сервера — фундамент трендов на панели.

    Схема узкая/расширяемая: одна строка на (guild_id, day, metric). Новая
    метрика модуля = новый ключ, без миграции. Запись идемпотентна: повторный
    снапшот того же дня перезаписывает значение (upsert)."""

    @abstractmethod
    async def record(self, guild_id: int, day: date, values: dict[str, float]) -> None:
        """Идемпотентно записать значения метрик за день (upsert по ключу)."""
        ...

    @abstractmethod
    async def series(self, guild_id: int, since: date) -> dict[str, list[tuple[date, float]]]:
        """Серии по метрикам, начиная с `since` включительно, старые→новые.
        Ключ — имя метрики, значение — точки (день, значение)."""
        ...
