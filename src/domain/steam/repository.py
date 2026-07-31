from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.steam.entities import TrackedGame


class ITrackedGameRepository(ABC):
    """Отслеживаемые игры Steam всех серверов."""

    @abstractmethod
    async def add(self, game: TrackedGame) -> TrackedGame:
        """Сохраняет и возвращает игру с заполненным id."""

    @abstractmethod
    async def get(self, guild_id: int, appid: int) -> TrackedGame | None: ...

    @abstractmethod
    async def list_for_guild(self, guild_id: int) -> list[TrackedGame]: ...

    @abstractmethod
    async def list_all(self) -> list[TrackedGame]:
        """Все игры всех серверов — для фонового опроса новостей."""

    @abstractmethod
    async def count_all(self) -> int: ...

    @abstractmethod
    async def remove(self, guild_id: int, appid: int) -> bool:
        """True — игра была и удалена; False — такой не было."""

    @abstractmethod
    async def mark_announced(self, game_id: int, gid: str, date: datetime) -> None:
        """Сдвигает отметку последней объявленной новости."""
