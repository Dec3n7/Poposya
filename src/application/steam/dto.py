from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class GameInfoDTO:
    """Карточка игры для шапки треда."""

    appid: int
    name: str
    short_description: str = ""
    header_image: str = ""
    store_url: str = ""


@dataclass(frozen=True)
class NewsItemDTO:
    """Новость Steam в терминах приложения (без формата ответа API).
    contents — исходный BBCode; рендер в Discord-markdown делает ког."""

    gid: str
    title: str
    url: str
    contents: str
    feedname: str
    feedlabel: str
    date: datetime
    author: str = ""
    is_external_url: bool = False

    @property
    def marker(self) -> tuple[datetime, str]:
        return (self.date, self.gid)

    @property
    def is_official(self) -> bool:
        """Официальная новость разработчика (обновление/патч/анонс), а не
        подмешанная в ленту внешняя статья прессы.

        Признак — фид «Community Announcements» самой игры. НЕ is_external_url:
        у многих игр (напр. CS2) официальные анонсы ведут ссылкой на свой сайт
        и помечены как внешние, оставаясь при этом дев-постами."""
        return self.feedname == "steam_community_announcements"


@dataclass(frozen=True)
class NewsPage:
    """Результат запроса новостей. ok=False — сеть/ошибка: состояние не трогаем."""

    items: list[NewsItemDTO] = field(default_factory=list)
    ok: bool = True


@dataclass(frozen=True)
class GameSnapshot:
    """Снимок игры при добавлении: карточка + самая свежая официальная новость
    (если есть). latest_news = None → объявлять пока нечего."""

    info: GameInfoDTO
    latest_news: NewsItemDTO | None
