import logging

import aiohttp

from src.infrastructure.cinema.provider import MovieInfo

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.kinopoisk.dev/v1.4/movie/search"


class KinopoiskClient:
    """Поиск фильмов через kinopoisk.dev (неофициальный API Кинопоиска).
    Бесплатный токен ~200 запросов/день; работает из РФ без VPN, русские
    описания полнее, чем у TMDB."""

    def __init__(self, api_key: str):
        self._api_key = api_key.strip()

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def search(self, query: str, limit: int = 5) -> list[MovieInfo]:
        if not self.enabled:
            return []
        headers = {"X-API-KEY": self._api_key}
        params = {"query": query[:200], "limit": str(limit), "page": "1"}
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(_SEARCH_URL, params=params, headers=headers) as resp:
                    if resp.status != 200:
                        logger.warning("Кинопоиск ответил %s", resp.status)
                        return []
                    data = await resp.json()
        except (aiohttp.ClientError, TimeoutError):
            logger.warning("Кинопоиск недоступен или не ответил вовремя")
            return []
        results: list[MovieInfo] = []
        for doc in (data.get("docs") or [])[:limit]:
            title = (
                doc.get("name") or doc.get("alternativeName") or doc.get("enName") or "Без названия"
            )
            year = doc.get("year")
            overview = (doc.get("description") or doc.get("shortDescription") or "").strip()
            poster = ((doc.get("poster") or {}).get("url")) or ""
            results.append(
                MovieInfo(
                    tmdb_id=int(doc.get("id") or 0),
                    title=title,
                    year=int(year) if year else None,
                    overview=overview,
                    poster_url=poster,
                )
            )
        return results
