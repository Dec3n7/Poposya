from abc import ABC, abstractmethod

from src.application.repos.dto import ReleasesPage, RepoInfoDTO


class IGitHubClient(ABC):
    """Клиент публичного GitHub REST API. Реализация — в infrastructure."""

    @abstractmethod
    async def get_repo(self, owner: str, name: str) -> RepoInfoDTO | None:
        """Карточка репозитория или None, если его нет (404)."""

    @abstractmethod
    async def list_releases(self, owner: str, name: str, etag: str | None = None) -> ReleasesPage:
        """Последние релизы, новейший первым. При переданном etag использует
        условный запрос (If-None-Match) и может вернуть not_modified."""
