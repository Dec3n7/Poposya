class MoodTracker:
    """Настроение персоны 0–100 на гильдию (in-memory).

    +2 активность в главном канале, +1 ответ на упоминание, −5 оскорбление.
    Дрейф: при активности — к 75, при тишине >2 часов — к 20 (вызывает
    фоновый цикл ActivityCog)."""

    ACTIVE_TARGET = 75.0
    IDLE_TARGET = 20.0
    DRIFT_RATE = 0.15  # доля пути к цели за один шаг дрейфа
    DEFAULT = 50.0

    def __init__(self) -> None:
        self._mood: dict[int, float] = {}

    def get(self, guild_id: int) -> int:
        return round(self._mood.get(guild_id, self.DEFAULT))

    def bump(self, guild_id: int, delta: float) -> None:
        current = self._mood.get(guild_id, self.DEFAULT)
        self._mood[guild_id] = max(0.0, min(100.0, current + delta))

    def drift(self, guild_id: int, active: bool) -> None:
        target = self.ACTIVE_TARGET if active else self.IDLE_TARGET
        current = self._mood.get(guild_id, self.DEFAULT)
        self._mood[guild_id] = current + (target - current) * self.DRIFT_RATE

    @staticmethod
    def describe(mood: int) -> str:
        if mood <= 30:
            return "мрачное, раздражённое — отвечаешь суше и колче обычного"
        if mood >= 65:
            return "хорошее — чуть теплее и разговорчивее обычного"
        return "ровное, обычное"
