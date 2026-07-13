import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MovieInfo:
    """Результат поиска фильма, общий для всех провайдеров.
    tmdb_id — внешний id провайдера (у Кинопоиска — их id)."""

    tmdb_id: int
    title: str
    year: int | None
    overview: str
    poster_url: str


class IMovieSearch(Protocol):
    @property
    def enabled(self) -> bool: ...

    async def search(self, query: str, limit: int = 5) -> list[MovieInfo]: ...


class FallbackMovieSearch:
    """Поиск с резервом: основной провайдер выбирается MOVIE_PROVIDER,
    второй настроенный пробуется, если основной недоступен (например,
    TMDB заблокирован по IP) или ничего не нашёл."""

    def __init__(self, primary: IMovieSearch, secondary: IMovieSearch):
        self._primary = primary
        self._secondary = secondary

    @property
    def enabled(self) -> bool:
        return self._primary.enabled or self._secondary.enabled

    async def search(self, query: str, limit: int = 5) -> list[MovieInfo]:
        for client in (self._primary, self._secondary):
            if not client.enabled:
                continue
            try:
                results = await client.search(query, limit)
            except Exception:
                logger.warning(
                    "Провайдер поиска фильмов упал, пробую следующий", exc_info=True
                )
                continue
            if results:
                return results
        return []
