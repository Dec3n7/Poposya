from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.finds.entities import CollectionItem, FindAttempt, NightFind


class INightFindRepository(ABC):
    """Активные и прошлые находки сервера."""

    @abstractmethod
    async def add(self, find: NightFind) -> NightFind:
        """Сохраняет и возвращает находку с заполненным id."""

    @abstractmethod
    async def save(self, find: NightFind) -> None: ...

    @abstractmethod
    async def get(self, find_id: int) -> NightFind | None: ...

    @abstractmethod
    async def get_by_message(self, message_id: int) -> NightFind | None: ...

    @abstractmethod
    async def get_active(self, guild_id: int, now: datetime) -> NightFind | None: ...

    @abstractmethod
    async def list_unclaimed(self, now: datetime) -> list[NightFind]:
        """Живые находки всех гильдий (для восстановления кнопок после рестарта)."""

    @abstractmethod
    async def claim_if_free(self, find_id: int, user_id: int, now: datetime) -> bool:
        """Атомарно записывает claimed_by, если находка ещё свободна.
        False — кто-то успел раньше."""


class ICollectionRepository(ABC):
    """Коллекции предметов пользователей."""

    @abstractmethod
    async def add(self, item: CollectionItem) -> None: ...

    @abstractmethod
    async def list_for_user(self, guild_id: int, user_id: int) -> list[CollectionItem]: ...

    @abstractmethod
    async def get_ungifted(
        self, guild_id: int, user_id: int, item_id: str
    ) -> CollectionItem | None:
        """Экземпляр предмета, который ещё не подарен (для /gift)."""

    @abstractmethod
    async def mark_gifted(self, collection_item_id: int, now: datetime) -> None: ...


class IFindAttemptRepository(ABC):
    """Попытки походов и прогулок: кулдауны и история."""

    @abstractmethod
    async def add(self, attempt: FindAttempt) -> None: ...

    @abstractmethod
    async def last_attempt_at(self, guild_id: int, user_id: int, kind: str) -> datetime | None: ...

    @abstractmethod
    async def has_attempted(self, find_id: int, user_id: int) -> bool:
        """Ходил ли пользователь уже к этой находке."""
