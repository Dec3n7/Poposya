from abc import ABC, abstractmethod
from datetime import date


class IMessageActivityRepository(ABC):
    """Почасовой счётчик сообщений сервера — фундамент хитмапа «активность по
    часам» и метрики «сообщения/день» на панели.

    Приватность by design: агрегат по (guild_id, дата, час в UTC), БЕЗ привязки
    к пользователю и без содержимого. Запись — идемпотентный инкремент (upsert
    count += delta): бот копит счётчик в памяти и периодически доливает пачкой,
    поэтому падение/рестарт теряет максимум один интервал, но не «зануляет» день."""

    @abstractmethod
    async def add(self, guild_id: int, buckets: dict[tuple[date, int], int]) -> None:
        """Прибавить счётчики к корзинам (день, час) -> сколько сообщений."""
        ...

    @abstractmethod
    async def daily(self, guild_id: int, since: date) -> list[tuple[date, int]]:
        """Сумма сообщений по дням, начиная с `since` включительно, старые→новые."""
        ...

    @abstractmethod
    async def hourly(self, guild_id: int, since: date) -> list[tuple[date, int, int]]:
        """Строки (день, час, счётчик) с `since` — для агрегации в сетку день×час
        недели на стороне приложения (weekday считается из даты)."""
        ...
