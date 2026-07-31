"""Рендер тела новости Steam (BBCode) в Discord-markdown + извлечение картинки.

Steam отдаёт анонсы в своём BBCode (`[b]`, `[list]`, `[img]`, `[url]`, `[h1]`,
`[p]`…), картинки — через токены `{STEAM_CLAN_IMAGE}`. Переводим в разметку,
понятную Discord, а первую картинку вытаскиваем для эмбеда. Идеальной точности
не нужно — нужна читаемость и картинка."""

import re

_CLAN_IMAGE_TOKENS = ("{STEAM_CLAN_IMAGE}", "{STEAM_CLAN_LOC_IMAGE}")
_CLAN_IMAGE_BASE = "https://clan.cloudflare.steamstatic.com/images"

_IMG_RE = re.compile(r"\[img\]\s*([^\[\]]+?)\s*\[/img\]", re.IGNORECASE)
# остаточные неизвестные теги; `(?!\()` бережёт метку уже собранной
# markdown-ссылки `[текст](url)` — её `[текст]` не тег
_TAG_RE = re.compile(r"\[/?[a-zA-Z][^\]]*\](?!\()")
_BLANKS_RE = re.compile(r"\n{3,}")
_TRAIL_RE = re.compile(r"[ \t]+\n")


def _resolve_tokens(text: str) -> str:
    for token in _CLAN_IMAGE_TOKENS:
        text = text.replace(token, _CLAN_IMAGE_BASE)
    return text


def extract_first_image(contents: str) -> str | None:
    """URL первой картинки анонса (с раскрытыми токенами) или None."""
    match = _IMG_RE.search(_resolve_tokens(contents or ""))
    if not match:
        return None
    url = match.group(1).strip()
    return url if url.startswith("http") else None


def to_markdown(contents: str) -> str:
    """BBCode -> Discord-markdown. Картинки убираются (для эмбеда берутся
    отдельно через extract_first_image)."""
    text = _resolve_tokens(contents or "")
    # Steam экранирует литеральные скобки в прозе (`\[ COLOGNE ]`). Прячем их за
    # плейсхолдеры, чтобы разбор тегов не принял их за BBCode; вернём в конце.
    text = text.replace("\\[", "\x00").replace("\\]", "\x01")
    text = _IMG_RE.sub("", text)

    # заголовки -> жирная строка (надёжнее заголовков markdown в эмбедах)
    text = re.sub(r"\[h[1-3]\](.*?)\[/h[1-3]\]", r"\n**\1**\n", text, flags=re.I | re.S)
    # ссылки (url в атрибуте Steam часто в кавычках — не тащим их в markdown)
    text = re.sub(
        r"""\[url=["']?([^\]"']+)["']?\](.*?)\[/url\]""", r"[\2](\1)", text, flags=re.I | re.S
    )
    text = re.sub(r"\[url\](.*?)\[/url\]", r"\1", text, flags=re.I | re.S)
    # цитаты и спойлеры
    text = re.sub(r"\[quote(?:=[^\]]*)?\](.*?)\[/quote\]", r"\n> \1\n", text, flags=re.I | re.S)
    text = re.sub(r"\[spoiler\](.*?)\[/spoiler\]", r"||\1||", text, flags=re.I | re.S)
    text = re.sub(r"\[code\](.*?)\[/code\]", r"\n```\n\1\n```\n", text, flags=re.I | re.S)
    # видео-превью Steam в тексте бесполезны — убираем целиком
    text = re.sub(
        r"\[previewyoutube=[^\]]*\](.*?)\[/previewyoutube\]", "", text, flags=re.I | re.S
    )

    # списки
    text = re.sub(r"\[/?(?:list|olist)\]", "\n", text, flags=re.I)
    text = re.sub(r"\[\*\]", "\n- ", text, flags=re.I)
    text = re.sub(r"\[/\*\]", "", text, flags=re.I)
    # абзацы и разделители
    text = re.sub(r"\[/?p\]", "\n", text, flags=re.I)
    text = re.sub(r"\[hr\]\[/hr\]|\[hr\]", "\n", text, flags=re.I)

    # парные простые теги-переключатели
    for tag, mark in (("b", "**"), ("i", "*"), ("u", "__"), ("strike", "~~"), ("s", "~~")):
        text = re.sub(rf"\[/?{tag}\]", mark, text, flags=re.I)

    # всё прочее в квадратных скобках — выкинуть, сохранив внутренний текст
    text = _TAG_RE.sub("", text)
    # вернуть литеральные скобки из плейсхолдеров
    text = text.replace("\x00", "[").replace("\x01", "]")

    # пункт списка и его текст часто разъезжаются (`[*]` + `[p]` дают «- \nтекст»)
    # — подтягиваем текст к маркеру
    text = re.sub(r"\n-[ \t]*\n+[ \t]*", "\n- ", text)

    text = _TRAIL_RE.sub("\n", text)
    text = _BLANKS_RE.sub("\n\n", text)
    return text.strip()


def render_news(contents: str) -> tuple[str, str | None]:
    """(markdown-текст, url первой картинки | None) — всё, что нужно эмбеду."""
    return to_markdown(contents), extract_first_image(contents)
