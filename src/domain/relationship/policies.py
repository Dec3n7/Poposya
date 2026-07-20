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

    def progress_to_next(self, points: int, is_exclusive: bool) -> float:
        """Доля прогресса внутри текущей ступени к следующей роли (0..1).
        На максимуме (следующей ступени нет) -> 1.0. Для прогресс-бара в панели."""
        nxt = self.next_threshold(points)
        if nxt is None:
            return 1.0
        idx = self.role_index(points, is_exclusive)
        if idx is None:
            lower = 0
        elif idx >= self.exclusive_role_index:
            lower = self.exclusive_threshold
        else:
            lower = self.thresholds[idx]
        span = nxt - lower
        if span <= 0:
            return 1.0
        return max(0.0, min(1.0, (points - lower) / span))
