from bisect import bisect_right
from dataclasses import dataclass


@dataclass(frozen=True)
class PointsToLevelPolicy:
    """Маппинг очков в роль-ступень и тон промпта (ТЗ 8.5).

    thresholds — пороги обычных ролей (100..1200); индекс роли считается
    по ним. Последний индекс (len(thresholds)) зарезервирован за
    «Единственным» и достигается только через эксклюзивность."""

    thresholds: tuple[int, ...] = (100, 250, 450, 700, 950, 1200)
    exclusive_threshold: int = 1250

    @property
    def exclusive_role_index(self) -> int:
        return len(self.thresholds)

    def role_index(self, points: int, is_exclusive: bool) -> int | None:
        """Индекс роли в списке имён (0..6) или None (0–49, без роли)."""
        if is_exclusive:
            return self.exclusive_role_index
        index = bisect_right(self.thresholds, points) - 1
        return index if index >= 0 else None

    def level(self, points: int, is_exclusive: bool) -> int:
        """Тон промпта 1–7: 1 до первого порога, +1 за каждый взятый порог,
        потолок 6. Тон 7 — только у эксклюзива."""
        if is_exclusive:
            return 7
        return min(6, 1 + bisect_right(self.thresholds, points))

    def next_threshold(self, points: int) -> int | None:
        """Сколько очков нужно до следующей роли (для /rank)."""
        for threshold in self.thresholds:
            if points < threshold:
                return threshold
        if points < self.exclusive_threshold:
            return self.exclusive_threshold
        return None
