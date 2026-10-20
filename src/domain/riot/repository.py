from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.riot.entities import TrackedRiotGame


class ITrackedRiotGameRepository(ABC):
    """Отслеживаемые игры Riot всех серверов."""

    @abstractmethod
    async def add(self, game: TrackedRiotGame) -> TrackedRiotGame:
        """Сохраняет и возвращает игру с заполненным id."""

    @abstractmethod
    async def get(self, guild_id: int, game: str) -> TrackedRiotGame | None: ...

    @abstractmethod
    async def list_for_guild(self, guild_id: int) -> list[TrackedRiotGame]: ...

    @abstractmethod
    async def list_all(self) -> list[TrackedRiotGame]:
        """Все игры всех серверов — для фонового опроса новостей."""

    @abstractmethod
    async def count_all(self) -> int: ...

    @abstractmethod
    async def remove(self, guild_id: int, game: str) -> bool:
        """True — игра была и удалена; False — такой не было."""

    @abstractmethod
    async def mark_announced(self, game_id: int, gid: str, date: datetime) -> None:
        """Сдвигает отметку последней объявленной новости."""
