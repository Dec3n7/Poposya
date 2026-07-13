"""Киноклуб: тонкий ког + компоненты. Разбит по образцу music/:
formatting (чистые хелперы), views (Discord-компоненты), cog (CinemaCog).

Публичные имена сохранены на уровне пакета, чтобы прежние импорты
`from ...cogs.cinema import CinemaCog` продолжали работать."""
from .cog import CinemaCog
from .formatting import _title_of, _trim, _ts
from .views import (
    CinemaCardView,
    CinemaRatingView,
    CinemaWatchedView,
    MoviePickView,
    NightPollView,
    ReviewModal,
)

__all__ = [
    "CinemaCog",
    "CinemaCardView",
    "CinemaRatingView",
    "CinemaWatchedView",
    "MoviePickView",
    "NightPollView",
    "ReviewModal",
    "_title_of",
    "_trim",
    "_ts",
]
