from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class RiotNewsItemDTO:
    """Новость Riot в терминах приложения (без формата страницы сайта).
    contents — уже конвертированный в Discord-markdown текст."""

    gid: str
    title: str
    url: str
    contents: str
    date: datetime
    kind: str = "weblink"  # weblink | youtube_video
    image_url: str = ""
    category: str = ""

    @property
    def marker(self) -> tuple[datetime, str]:
        return (self.date, self.gid)

    @property
    def is_video(self) -> bool:
        return self.kind == "youtube_video"


@dataclass(frozen=True)
class RiotNewsPage:
    """Результат запроса новостей. ok=False — сеть/контракт: состояние не трогаем."""

    items: list[RiotNewsItemDTO] = field(default_factory=list)
    ok: bool = True
    error: str = ""


@dataclass(frozen=True)
class RiotGameSnapshot:
    """Снимок игры при добавлении: подпись и самая свежая новость (если есть).
    latest_news = None → объявлять пока нечего."""

    game: str
    name: str
    latest_news: RiotNewsItemDTO | None
