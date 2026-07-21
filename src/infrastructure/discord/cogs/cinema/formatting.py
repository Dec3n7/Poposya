"""Чистые хелперы киноклуба: обрезка текста, заголовки, Discord-таймстампы,
регэкспы разбора времени/оценки. Без обращений к Discord API. Цвет эмбедов —
из персоны сервера (src.infrastructure.discord.accent)."""

import re
from datetime import datetime

from src.domain.cinema.entities import MovieEntry

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_DATE_RE = re.compile(r"^(\d{1,2})[.\-/](\d{1,2})$")
_SCORE_RE = re.compile(r"\b(\d{1,2})\s*/\s*10")


def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _title_of(entry: MovieEntry) -> str:
    return f"{entry.title} ({entry.year})" if entry.year else entry.title


def _ts(dt: datetime, style: str = "R") -> str:
    return f"<t:{int(dt.timestamp())}:{style}>"
