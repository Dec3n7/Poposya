from abc import ABC, abstractmethod

from src.domain.appeals.entities import Appeal


class IAppealRepository(ABC):
    @abstractmethod
    async def add(self, appeal: Appeal) -> Appeal:
        """Создать апелляцию (возвращает с проставленным id)."""
        ...

    @abstractmethod
    async def get(self, appeal_id: int) -> Appeal | None: ...

    @abstractmethod
    async def get_pending(self, guild_id: int, user_id: int) -> Appeal | None:
        """Активная (pending) апелляция участника — антиспам: одна за раз."""
        ...

    @abstractmethod
    async def list_pending(self, guild_id: int) -> list[Appeal]:
        """Открытые апелляции сервера (для панели), старые→новые."""
        ...

    @abstractmethod
    async def save(self, appeal: Appeal) -> None:
        """Сохранить изменения (статус/resolver/review_message_id)."""
        ...
