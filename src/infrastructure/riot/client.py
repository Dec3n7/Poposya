"""Клиент публичных новостных страниц сайтов Riot.

Riot не даёт новостного API и RSS, но сайты — Next.js SSG: в HTML встроен
`<script id="__NEXT_DATA__">` с полной лентой хедлайнов. Читаем JSON-блоб, а не
DOM: контракт стабильнее и разбирать проще. Ошибки изолируются по item — одна
битая запись не роняет ленту; fail-soft всей страницы только при отсутствии
blade или если не распарсен ни один item.

Один и тот же разбор обслуживает все игры Riot: структура лент у них общая.
Поля `category` и `description` встречаются и объектами, и строками, а
`action.payload.url` у внутренних новостей относительный — его достраиваем по
`page.baseUrl`. Оба варианта формата поддержаны, чтобы изменение контракта на
стороне Riot не ломало парсер молча."""

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import aiohttp

from src.application.riot.dto import RiotNewsItemDTO, RiotNewsPage
from src.application.riot.interfaces import IRiotNewsClient
from src.domain.riot.refs import RiotGame, news_url
from src.infrastructure.riot.html import extract_first_image, to_markdown

logger = logging.getLogger(__name__)

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL
)
_TIMEOUT_SECONDS = 20.0
_USER_AGENT = "Mozilla/5.0 (compatible; PoposyaBot/1.0)"
_ABSOLUTE_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)


def _find_news_blade(node: Any) -> dict | None:
    """Первый blade с fragmentId == "news" и списком items."""
    if isinstance(node, dict):
        if node.get("fragmentId") == "news" and isinstance(node.get("items"), list):
            return node
        for value in node.values():
            found = _find_news_blade(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_news_blade(value)
            if found is not None:
                return found
    return None


def _find_base_url(payload: Any) -> str:
    """`page.baseUrl` из pageProps — база для относительных ссылок. Иначе пусто."""
    if not isinstance(payload, dict):
        return ""
    page = payload.get("props", {}).get("pageProps", {}).get("page", {})
    if isinstance(page, dict):
        base = page.get("baseUrl")
        if isinstance(base, str):
            return base.strip()
    return ""


def _parse_date(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _body_text(value: Any) -> str:
    """HTML описания: строка напрямую или объект `{type, body}`."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        body = value.get("body")
        if isinstance(body, str):
            return body
    return ""


def _category_label(value: Any) -> str:
    """Подпись категории: строка напрямую или объект `{title, machineName}`."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        title = value.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
        machine = value.get("machineName")
        if isinstance(machine, str):
            return machine.strip()
    return ""


def _media_url(value: Any) -> str:
    if isinstance(value, dict):
        url = value.get("url")
        if isinstance(url, str):
            return url.strip()
    return ""


def _absolute(url: str, base: str) -> str:
    """Относительную ссылку достраиваем до абсолютной по базе сайта."""
    if not url:
        return ""
    if _ABSOLUTE_URL_RE.match(url):
        return url
    return urljoin(base or "", url) if base else url


def _gid(item: dict, title: str, published_raw: str, url: str) -> str:
    analytics = item.get("analytics")
    content_id = analytics.get("contentId") if isinstance(analytics, dict) else None
    if isinstance(content_id, str) and content_id.strip():
        return content_id.strip()
    if url:
        return url
    digest = hashlib.sha1(f"{title}|{published_raw}".encode()).hexdigest()
    return digest[:16]


def _parse_item(raw: Any, base_url: str) -> RiotNewsItemDTO | None:
    if not isinstance(raw, dict):
        return None
    title = raw.get("title")
    title = title.strip() if isinstance(title, str) else ""
    published = _parse_date(raw.get("publishedAt"))
    url = ""
    kind = "weblink"
    action = raw.get("action")
    if isinstance(action, dict):
        action_type = action.get("type")
        if isinstance(action_type, str) and action_type:
            kind = action_type
        payload = action.get("payload")
        if isinstance(payload, dict):
            raw_url = payload.get("url")
            url = _absolute(raw_url.strip(), base_url) if isinstance(raw_url, str) else ""
    if not title or published is None:
        logger.warning(
            "Новость Riot пропущена: нет заголовка или даты",
            extra={"title": title, "url": url},
        )
        return None

    image = _media_url(raw.get("media")) or _media_url(raw.get("imageMedia"))
    description = _body_text(raw.get("description"))
    if not image:
        image = extract_first_image(description) or ""

    return RiotNewsItemDTO(
        gid=_gid(raw, title, str(raw.get("publishedAt") or ""), url),
        title=title,
        url=url,
        contents=to_markdown(description),
        date=published,
        kind=kind if kind in ("weblink", "youtube_video") else "weblink",
        image_url=image,
        category=_category_label(raw.get("category")),
    )


def parse_next_data(payload: Any) -> RiotNewsPage:
    """Разбор JSON из `__NEXT_DATA__`. Публичный для contract-теста."""
    blade = _find_news_blade(payload)
    if blade is None:
        return RiotNewsPage(ok=False, error="в __NEXT_DATA__ нет blade «news»")
    raw_items = blade.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return RiotNewsPage(items=[])
    base_url = _find_base_url(payload)
    items: list[RiotNewsItemDTO] = []
    for raw in raw_items:
        item = _parse_item(raw, base_url)
        if item is not None:
            items.append(item)
    if not items:
        return RiotNewsPage(ok=False, error="ни одна новость не разобрана")
    return RiotNewsPage(items=sorted(items, key=lambda n: n.marker, reverse=True))


def parse_html(html: str) -> RiotNewsPage:
    match = _NEXT_DATA_RE.search(html or "")
    if not match:
        return RiotNewsPage(ok=False, error="в HTML нет __NEXT_DATA__")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return RiotNewsPage(ok=False, error="__NEXT_DATA__ не является JSON")
    return parse_next_data(payload)


class RiotNewsClient(IRiotNewsClient):
    def __init__(self, timeout_seconds: float = _TIMEOUT_SECONDS, user_agent: str = _USER_AGENT):
        self._timeout = timeout_seconds
        self._user_agent = user_agent

    async def get_news(self, game: RiotGame, locale: str) -> RiotNewsPage:
        try:
            url = news_url(game, locale)
        except ValueError as exc:
            return RiotNewsPage(ok=False, error=str(exc))
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": locale,
        }
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return RiotNewsPage(ok=False, error=f"HTTP {resp.status}")
                    html = await resp.text()
        except (TimeoutError, aiohttp.ClientError) as exc:
            logger.warning("Riot-запрос не удался", extra={"url": url, "error": str(exc)})
            return RiotNewsPage(ok=False, error=str(exc))
        page = parse_html(html)
        if not page.ok:
            logger.warning("Лента Riot не разобрана", extra={"url": url, "error": page.error})
        return page
