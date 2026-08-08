from abc import ABC, abstractmethod

from src.domain.achievements.entities import UnlockedAchievement


class IAchievementRepository(ABC):
    @abstractmethod
    async def unlocked_ids(self, user_id: int, guild_id: int) -> set[str]:
        """Идентификаторы уже открытых ачивок участника — вход для newly_unlocked."""

    @abstractmethod
    async def add(self, unlocked: UnlockedAchievement) -> None:
        """Идемпотентно: повторная выдача той же ачивки не плодит дубль и не падает."""

    @abstractmethod
    async def list_for_user(self, user_id: int, guild_id: int) -> list[UnlockedAchievement]:
        """Открытые ачивки участника, новые сверху — для витрины /achievements."""
