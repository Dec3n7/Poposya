from abc import ABC, abstractmethod

from src.domain.botprofile.entities import BotProfile


class IBotProfileRepository(ABC):
    """Пер-серверный профиль бота: одна строка на гильдию (upsert)."""

    @abstractmethod
    async def get(self, guild_id: int) -> BotProfile | None: ...

    @abstractmethod
    async def save(self, profile: BotProfile) -> None: ...
