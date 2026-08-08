import base64
import logging
import re
import time

import aiohttp

logger = logging.getLogger(__name__)

_OEMBED_URL = "https://open.spotify.com/oembed"
_TOKEN_URL = "https://accounts.spotify.com/api/token"
_API = "https://api.spotify.com/v1"
# open.spotify.com/playlist/ID, /album/ID, в т.ч. с префиксом /intl-xx/ и ?si=…
_COLLECTION_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?(playlist|album)/([A-Za-z0-9]+)")


class SpotifyLinkResolver:
    """Spotify без прямого стриминга: узнаём названия и ищем на YouTube.

    Одиночные ссылки — через oEmbed (ключ не нужен). Плейлисты и альбомы — через
    Spotify Web API (Client Credentials), поэтому требуют SPOTIFY_CLIENT_ID /
    SPOTIFY_CLIENT_SECRET. Без ключей поддержка коллекций просто выключена, а
    треки продолжают работать.
    """

    def __init__(self, client_id: str = "", client_secret: str = ""):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expires_at = 0.0

    @property
    def has_api_credentials(self) -> bool:
        return bool(self._client_id and self._client_secret)

    @staticmethod
    def is_spotify_link(query: str) -> bool:
        return "open.spotify.com/" in query

    @staticmethod
    def is_track_link(query: str) -> bool:
        return "/track/" in query

    @staticmethod
    def is_collection_link(query: str) -> bool:
        """Плейлист или альбом — то, для чего нужен API."""
        return _COLLECTION_RE.search(query) is not None

    async def search_query_for(self, url: str) -> str | None:
        """Поисковый запрос по одиночной Spotify-ссылке (oEmbed) или None."""
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

    async def track_queries_for(self, url: str, limit: int = 50) -> list[str]:
        """Список поисковых запросов «исполнитель название» по треки плейлиста
        или альбома, не длиннее limit. [] — нет ключей, битая ссылка или сбой
        API (звонящий отличает первое через has_api_credentials)."""
        match = _COLLECTION_RE.search(url)
        if not match or not self.has_api_credentials:
            return []
        kind, spotify_id = match.group(1), match.group(2)
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                token = await self._access_token(session)
                if token is None:
                    return []
                if kind == "playlist":
                    return await self._playlist_tracks(session, spotify_id, token, limit)
                return await self._album_tracks(session, spotify_id, token, limit)
        except (aiohttp.ClientError, TimeoutError):
            logger.warning("Spotify Web API недоступен или не ответил вовремя")
            return []

    async def _access_token(self, session: aiohttp.ClientSession) -> str | None:
        """Client Credentials-токен с кэшем: он живёт час, дёргать /token на
        каждый плейлист незачем. Запас 60с — чтобы не отдать протухший."""
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        auth = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        async with session.post(
            _TOKEN_URL,
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {auth}"},
        ) as resp:
            if resp.status != 200:
                logger.warning("Spotify /token вернул %s (проверь CLIENT_ID/SECRET)", resp.status)
                return None
            data = await resp.json()
        self._token = data.get("access_token")
        self._token_expires_at = time.monotonic() + int(data.get("expires_in", 3600)) - 60
        return self._token

    async def _playlist_tracks(
        self, session: aiohttp.ClientSession, playlist_id: str, token: str, limit: int
    ) -> list[str]:
        queries: list[str] = []
        # fields сужает ответ до нужного, next ведёт постранично
        url: str | None = (
            f"{_API}/playlists/{playlist_id}/tracks"
            "?fields=items(track(name,artists(name))),next&limit=100"
        )
        headers = {"Authorization": f"Bearer {token}"}
        while url and len(queries) < limit:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning("Spotify playlist вернул %s", resp.status)
                    break
                data = await resp.json()
            for item in data.get("items") or []:
                query = _track_query((item or {}).get("track"))
                if query:
                    queries.append(query)
            url = data.get("next")
        return queries[:limit]

    async def _album_tracks(
        self, session: aiohttp.ClientSession, album_id: str, token: str, limit: int
    ) -> list[str]:
        queries: list[str] = []
        url: str | None = f"{_API}/albums/{album_id}/tracks?limit=50"
        headers = {"Authorization": f"Bearer {token}"}
        while url and len(queries) < limit:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning("Spotify album вернул %s", resp.status)
                    break
                data = await resp.json()
            for track in data.get("items") or []:
                query = _track_query(track)
                if query:
                    queries.append(query)
            url = data.get("next")
        return queries[:limit]


def _track_query(track: dict | None) -> str | None:
    """«исполнитель название» из объекта трека Spotify. None — трек недоступен
    (бывает в плейлистах: удалён из каталога региона)."""
    if not track:
        return None
    name = (track.get("name") or "").strip()
    if not name:
        return None
    artists = " ".join(a.get("name", "") for a in track.get("artists") or [] if a.get("name"))
    return f"{artists} {name}".strip()
