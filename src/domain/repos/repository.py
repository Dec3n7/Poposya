from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.repos.entities import TrackedRepo


class ITrackedRepoRepository(ABC):
    """Отслеживаемые репозитории всех серверов."""

    @abstractmethod
    async def add(self, repo: TrackedRepo) -> TrackedRepo:
        """Сохраняет и возвращает репозиторий с заполненным id."""

    @abstractmethod
    async def get(self, guild_id: int, owner: str, name: str) -> TrackedRepo | None: ...

    @abstractmethod
    async def list_for_guild(self, guild_id: int) -> list[TrackedRepo]: ...

    @abstractmethod
    async def list_all(self) -> list[TrackedRepo]:
        """Все репозитории всех серверов — для фонового опроса релизов."""

    @abstractmethod
    async def count_all(self) -> int:
        """Сколько репозиториев отслеживается суммарно (для бюджета лимита GitHub)."""

    @abstractmethod
    async def remove(self, guild_id: int, owner: str, name: str) -> bool:
        """True — репозиторий был и удалён; False — такого не было."""

    @abstractmethod
    async def mark_announced(
        self, repo_id: int, release_id: int, published_at: datetime, etag: str | None
    ) -> None:
        """Сдвигает отметку последнего объявленного релиза. etag обновляется,
        только когда передан не-None (после того как все новые релизы разосланы)."""

    @abstractmethod
    async def save_etag(self, repo_id: int, etag: str) -> None:
        """Запоминает ETag ответа, когда новых релизов не было (для 304)."""
