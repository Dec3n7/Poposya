import logging

import aiohttp

from src.infrastructure.cinema.provider import MovieInfo

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
_POSTER_BASE = "https://image.tmdb.org/t/p/w500"


class TmdbClient:
    """Поиск фильмов через TMDB (бесплатный API-ключ с themoviedb.org).
    Без ключа киноклуб работает в текстовом режиме — без постеров и описаний."""

    def __init__(self, api_key: str):
        self._api_key = api_key.strip()

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def search(self, query: str, limit: int = 5) -> list[MovieInfo]:
        if not self.enabled:
            return []
        params = {
            "api_key": self._api_key,
            "query": query[:200],
            "language": "ru-RU",
            "include_adult": "false",
        }
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(_SEARCH_URL, params=params) as resp:
                    if resp.status != 200:
                        logger.warning("TMDB ответил %s", resp.status)
                        return []
                    data = await resp.json()
        except (aiohttp.ClientError, TimeoutError):
            logger.warning("TMDB недоступен или не ответил вовремя")
            return []
        results = []
        for item in (data.get("results") or [])[:limit]:
            release = item.get("release_date") or ""
            poster_path = item.get("poster_path") or ""
            results.append(
                MovieInfo(
                    tmdb_id=item["id"],
                    title=item.get("title") or item.get("original_title") or "Без названия",
                    year=int(release[:4]) if release[:4].isdigit() else None,
                    overview=(item.get("overview") or "").strip(),
                    poster_url=f"{_POSTER_BASE}{poster_path}" if poster_path else "",
                )
            )
        return results
