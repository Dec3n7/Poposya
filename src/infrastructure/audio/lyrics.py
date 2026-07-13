import logging
import re

import aiohttp

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://lrclib.net/api/search"

# строка LRC: [mm:ss.xx] текст
_LRC_LINE = re.compile(r"\[(\d+):(\d{2}(?:\.\d+)?)\](.*)")

# мусор YouTube-заголовков, ломающий поиск по LRCLIB
_BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}|【[^】]*】")
# японские кавычки: содержимое сохраняем — там обычно само название
_JP_QUOTES = re.compile(r"[「」『』]")
_SEPARATORS = re.compile(r"\s*(?:--+|—|–|\||/)\s*")
_JUNK_WORDS = re.compile(
    r"\b(official\s+music\s+video|official\s+(?:video|audio)|music\s+video|"
    r"lyric\s+video|official|video|audio|lyrics?|lyric|visualizer|mv|m/v|hd|4k|"
    r"remaster(?:ed)?|prod\.?)\b",
    re.IGNORECASE,
)
_TOPIC_SUFFIX = re.compile(r"\s*-\s*topic$", re.IGNORECASE)


def clean_track_title(title: str) -> str:
    """«Lil Peep -- 16 Lines (Official Video)» -> «Lil Peep 16 Lines»."""
    text = _BRACKETS.sub(" ", title)
    text = _JP_QUOTES.sub(" ", text)
    text = _SEPARATORS.sub(" ", text)
    text = _JUNK_WORDS.sub(" ", text)
    text = text.replace("«", " ").replace("»", " ").replace('"', " ")
    return " ".join(text.split()).strip(" -–—")


def build_queries(title: str, uploader: str | None = None) -> list[str]:
    """Кандидаты для поиска: чистый заголовок, +канал, сырой заголовок."""
    queries: list[str] = []
    cleaned = clean_track_title(title)
    if cleaned:
        queries.append(cleaned)
    if uploader:
        channel = _TOPIC_SUFFIX.sub("", clean_track_title(uploader))
        if channel and channel.lower() not in cleaned.lower():
            queries.append(f"{channel} {cleaned}")
    if title.strip() and title.strip() not in queries:
        queries.append(title.strip())
    return queries


def parse_lrc(text: str) -> list[tuple[float, str]]:
    """LRC -> [(секунда, строка)], по возрастанию времени."""
    lines: list[tuple[float, str]] = []
    for raw in text.splitlines():
        match = _LRC_LINE.match(raw.strip())
        if not match:
            continue
        seconds = int(match.group(1)) * 60 + float(match.group(2))
        content = match.group(3).strip()
        if content:
            lines.append((seconds, content))
    lines.sort(key=lambda item: item[0])
    return lines


def group_blocks(
    lines: list[tuple[float, str]], size: int = 4
) -> list[tuple[float, list[str]]]:
    """Абзацы по size строк: (время начала абзаца, строки)."""
    blocks: list[tuple[float, list[str]]] = []
    for i in range(0, len(lines), size):
        chunk = lines[i:i + size]
        blocks.append((chunk[0][0], [text for _, text in chunk]))
    return blocks


class LrclibLyricsClient:
    """Тексты песен через открытый API LRCLIB (без ключа); хорошо покрывает
    в том числе японскую и русскую музыку. syncedLyrics — LRC с таймкодами."""

    async def _search(self, query: str) -> list[dict]:
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(_SEARCH_URL, params={"q": query[:200]}) as resp:
                    if resp.status != 200:
                        return []
                    return await resp.json() or []
        except (aiohttp.ClientError, TimeoutError):
            logger.warning("LRCLIB недоступен или не ответил вовремя")
            return []

    async def _find_field(self, title: str, uploader: str | None, field: str) -> str | None:
        for query in build_queries(title, uploader):
            for item in await self._search(query):
                value = (item.get(field) or "").strip()
                if value:
                    return value
        return None

    async def find_lyrics(self, title: str, uploader: str | None = None) -> str | None:
        return await self._find_field(title, uploader, "plainLyrics")

    async def find_synced(self, title: str, uploader: str | None = None) -> str | None:
        return await self._find_field(title, uploader, "syncedLyrics")

    async def find_both(
        self, title: str, uploader: str | None = None
    ) -> tuple[str | None, str | None]:
        """(synced, plain) за минимум запросов — для префетча в кэш."""
        synced: str | None = None
        plain: str | None = None
        for query in build_queries(title, uploader):
            for item in await self._search(query):
                if synced is None:
                    synced = (item.get("syncedLyrics") or "").strip() or None
                if plain is None:
                    plain = (item.get("plainLyrics") or "").strip() or None
                if synced and plain:
                    return synced, plain
            if synced or plain:
                break  # этот запрос уже дал результат — хуже не ищем
        return synced, plain
