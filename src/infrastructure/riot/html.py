"""HTML описания новости Riot -> Discord-markdown + извлечение картинки.

Подмножество, которое реально встречается в ленте: параграфы, br, жирный и
курсив, ссылки, списки, заголовки, цитаты и код. Полноценный HTML-рендер не
нужен — нужна читаемость в эмбеде и первая картинка для баннера.
"""

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any

_WS_RE = re.compile(r"\s+")
_IMG_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)
_BLANKS_RE = re.compile(r"\n{3,}")
_TRAIL_RE = re.compile(r"[ \t]+\n")

_BLOCK_START = frozenset({"p", "div", "section", "figure", "article"})
_BLOCK_END = frozenset({"p", "div", "section", "figure", "article"})
_HEADINGS = frozenset({"h1", "h2", "h3", "h4"})


class _MarkdownParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hrefs: list[str] = []
        self._lists: list[dict[str, Any]] = []
        self._in_pre = False

    def _emit(self, text: str) -> None:
        self.parts.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_START:
            self._emit("\n\n")
        elif tag == "br":
            self._emit("\n")
        elif tag in _HEADINGS:
            self._emit("\n\n**")
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag in ("em", "i"):
            self._emit("*")
        elif tag in ("s", "strike", "del"):
            self._emit("~~")
        elif tag == "a":
            self._hrefs.append(dict(attrs).get("href") or "")
            self._emit("[")
        elif tag == "blockquote":
            self._emit("\n> ")
        elif tag in ("ul", "ol"):
            self._emit("\n")
            self._lists.append({"ordered": tag == "ol", "n": 0})
        elif tag == "li":
            depth = max(len(self._lists) - 1, 0)
            if self._lists and self._lists[-1]["ordered"]:
                self._lists[-1]["n"] = int(self._lists[-1]["n"]) + 1
                marker = f"{self._lists[-1]['n']}. "
            else:
                marker = "- "
            self._emit("\n" + "  " * depth + marker)
        elif tag == "pre":
            self._emit("\n```\n")
            self._in_pre = True
        elif tag == "code" and not self._in_pre:
            self._emit("`")
        elif tag == "img":
            return

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_END:
            self._emit("\n\n")
        elif tag in _HEADINGS:
            self._emit("**\n\n")
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag in ("em", "i"):
            self._emit("*")
        elif tag in ("s", "strike", "del"):
            self._emit("~~")
        elif tag == "a":
            href = self._hrefs.pop() if self._hrefs else ""
            self._emit(f"]({href})" if href else "]")
        elif tag in ("ul", "ol"):
            if self._lists:
                self._lists.pop()
            self._emit("\n")
        elif tag == "blockquote":
            self._emit("\n")
        elif tag == "pre":
            self._emit("\n```\n")
            self._in_pre = False
        elif tag == "code" and not self._in_pre:
            self._emit("`")

    def handle_data(self, data: str) -> None:
        if self._in_pre:
            self._emit(data)
            return
        text = _WS_RE.sub(" ", data)
        if text.strip():
            self._emit(text)


def to_markdown(html: str) -> str:
    """HTML -> Discord-markdown. Картинки убираются (баннер берётся отдельно)."""
    if not html:
        return ""
    parser = _MarkdownParser()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    text = _TRAIL_RE.sub("\n", text)
    text = _BLANKS_RE.sub("\n\n", text)
    return text.strip()


def extract_first_image(html: str) -> str | None:
    """URL первой картинки описания или None."""
    match = _IMG_RE.search(html or "")
    if not match:
        return None
    url = unescape(match.group(1)).strip()
    return url if url.startswith(("http://", "https://")) else None
