"""Клиент публичных API Steam на aiohttp.

Новости — официальный `ISteamNews/GetNewsForApp` (ключ не нужен). Карточка игры
(имя, header, описание) — сторфронт `appdetails` (неофициальный, но открытый).
Сессия создаётся на запрос — опрос идёт раз в часы, накладные расходы ничтожны."""

import logging
from datetime import UTC, datetime

import aiohttp

from src.application.steam.dto import GameInfoDTO, NewsItemDTO, NewsPage
from src.application.steam.interfaces import ISteamClient
from src.domain.steam.refs import store_url

logger = logging.getLogger(__name__)

_NEWS_API = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v0002/"
_APPDETAILS = "https://store.steampowered.com/api/appdetails"


def _parse_news_item(item: dict) -> NewsItemDTO | None:
    date = item.get("date")
    if not isinstance(date, int | float):
        return None
    return NewsItemDTO(
        gid=str(item.get("gid") or ""),
        title=item.get("title") or "",
        url=item.get("url") or "",
        contents=item.get("contents") or "",
        feedname=item.get("feedname") or "",
        feedlabel=item.get("feedlabel") or "",
        date=datetime.fromtimestamp(int(date), tz=UTC),
        author=item.get("author") or "",
        is_external_url=bool(item.get("is_external_url")),
    )


def _parse_game(appid: int, data: dict) -> GameInfoDTO | None:
    if not data.get("name"):
        return None
    return GameInfoDTO(
        appid=appid,
        name=data.get("name") or "",
        short_description=data.get("short_description") or "",
        header_image=data.get("header_image") or "",
        store_url=store_url(appid),
    )


class SteamClient(ISteamClient):
    # storefront appdetails (store.steampowered.com) отвечает заметно медленнее
    # официального api.steampowered.com (наблюдалось ~10с). Даём ему отдельный
    # длинный таймаут, чтобы медленный ответ не превращался в ложное «не найдено».
    def __init__(
        self,
        timeout_seconds: float = 10.0,
        details_timeout_seconds: float = 25.0,
        user_agent: str = "PoposyaBot",
    ):
        self._timeout = timeout_seconds
        self._details_timeout = details_timeout_seconds
        self._user_agent = user_agent

    async def _get_json(
        self, url: str, params: dict[str, str], timeout_seconds: float | None = None
    ) -> tuple[object, int]:
        """(тело, статус). Статус 0 — сеть/таймаут/не-JSON."""
        try:
            timeout = aiohttp.ClientTimeout(total=timeout_seconds or self._timeout)
            headers = {"User-Agent": self._user_agent}
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        # Steam иногда отдаёт JSON с text/html — не привязываемся к типу
                        return await resp.json(content_type=None), 200
                    return None, resp.status
        except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
            logger.warning("Steam-запрос не удался", extra={"url": url, "error": str(exc)})
            return None, 0

    async def get_game(self, appid: int) -> GameInfoDTO | None:
        # cc=us — самый широкий каталог (региональные ограничения не прячут игру);
        # в замерах ещё и заметно быстрее отвечал
        data, status = await self._get_json(
            _APPDETAILS,
            {"appids": str(appid), "cc": "us", "l": "english"},
            timeout_seconds=self._details_timeout,
        )
        if status != 200 or not isinstance(data, dict):
            return None
        node = data.get(str(appid))
        if not isinstance(node, dict) or not node.get("success"):
            return None
        payload = node.get("data")
        return _parse_game(appid, payload) if isinstance(payload, dict) else None

    async def get_news(self, appid: int) -> NewsPage:
        data, status = await self._get_json(
            _NEWS_API,
            {"appid": str(appid), "count": "20", "maxlength": "0", "format": "json"},
        )
        if status != 200 or not isinstance(data, dict):
            return NewsPage(ok=False)
        raw = (data.get("appnews") or {}).get("newsitems") or []
        items = [n for it in raw if isinstance(it, dict) and (n := _parse_news_item(it))]
        return NewsPage(items=items, ok=True)
