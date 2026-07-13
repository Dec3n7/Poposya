import logging

import aiohttp

logger = logging.getLogger(__name__)

_OEMBED_URL = "https://open.spotify.com/oembed"


class SpotifyLinkResolver:
    """Одиночные Spotify-ссылки без API-ключа: oEmbed отдаёт название трека,
    дальше — обычный поиск на YouTube. Плейлисты/альбомы требуют Spotify API
    (SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET — задел в .env)."""

    @staticmethod
    def is_spotify_link(query: str) -> bool:
        return "open.spotify.com/" in query

    @staticmethod
    def is_track_link(query: str) -> bool:
        return "open.spotify.com/track/" in query or "/intl-" in query and "/track/" in query

    async def search_query_for(self, url: str) -> str | None:
        """Возвращает поисковый запрос для YouTube или None."""
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(_OEMBED_URL, params={"url": url}) as resp:
                    if resp.status != 200:
                        logger.info("Spotify oEmbed вернул %s", resp.status)
                        return None
                    data = await resp.json()
        except (aiohttp.ClientError, TimeoutError):
            logger.warning("Spotify oEmbed недоступен или не ответил вовремя")
            return None
        title = (data.get("title") or "").strip()
        author = (data.get("author_name") or "").strip()
        if not title:
            return None
        return f"{author} {title}".strip()
