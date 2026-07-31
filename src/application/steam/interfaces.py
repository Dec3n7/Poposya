from abc import ABC, abstractmethod

from src.application.steam.dto import GameInfoDTO, NewsPage


class ISteamClient(ABC):
    """Клиент публичных API Steam. Реализация — в infrastructure."""

    @abstractmethod
    async def get_game(self, appid: int) -> GameInfoDTO | None:
        """Карточка игры или None, если appid не найден / не игра."""

    @abstractmethod
    async def get_news(self, appid: int) -> NewsPage:
        """Последние новости приложения, новейшая первой."""
