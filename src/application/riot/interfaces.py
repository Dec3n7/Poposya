from abc import ABC, abstractmethod

from src.application.riot.dto import RiotNewsPage
from src.domain.riot.refs import RiotGame


class IRiotNewsClient(ABC):
    """Клиент публичной новостной страницы Riot. Реализация — в infrastructure."""

    @abstractmethod
    async def get_news(self, game: RiotGame, locale: str) -> RiotNewsPage:
        """Лента новостей игры, новейшая первой."""
