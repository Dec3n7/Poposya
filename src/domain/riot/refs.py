"""Игры семьи Riot и детерминированные ссылки на их новостные ленты.

Каждая игра — отдельный сайт-витрина на Next.js: у него свой домен, свой
набор локалей и своя дефолт-локаль (например, у 2XKO нет русского). Ленты всех
сайтов отдают одну и ту же структуру `__NEXT_DATA__` (blade «news»), поэтому
новый источник добавляется значением в `RiotGame` — без новой команды, без
новых настроек и без правок парсера.

Legends of Runeterra намеренно отсутствует: playruneterra.com — клиентский SPA
без встроенного `__NEXT_DATA__`, в единую схему он не укладывается."""

from dataclasses import dataclass
from enum import StrEnum

# Общая локаль по умолчанию для игр, где русский доступен.
DEFAULT_LOCALE = "ru-ru"


@dataclass(frozen=True)
class _GameSpec:
    label: str
    # Шаблон URL новостной страницы: {locale} подставляется как есть.
    news_url: str
    # Локаль, если серверная настройка не подходит игре.
    default_locale: str


# Порядок ключей = порядок в /riot add и в /riot list.
_GAMES: dict[str, _GameSpec] = {
    "valorant": _GameSpec("Valorant", "https://playvalorant.com/{locale}/news/", "ru-ru"),
    "lol": _GameSpec(
        "League of Legends", "https://www.leagueoflegends.com/{locale}/news/", "ru-ru"
    ),
    "tft": _GameSpec(
        "Teamfight Tactics",
        "https://teamfighttactics.leagueoflegends.com/{locale}/news/",
        "ru-ru",
    ),
    "wildrift": _GameSpec(
        "Wild Rift", "https://wildrift.leagueoflegends.com/{locale}/news/", "ru-ru"
    ),
    "2xko": _GameSpec("2XKO", "https://2xko.riotgames.com/{locale}/news/", "en-us"),
}

GAMES: tuple[str, ...] = tuple(_GAMES)


class RiotGame(StrEnum):
    VALORANT = "valorant"
    LOL = "lol"
    TFT = "tft"
    WILDRIFT = "wildrift"
    TWO_XKO = "2xko"

    @property
    def label(self) -> str:
        return _GAMES[self.value].label

    @property
    def default_locale(self) -> str:
        return _GAMES[self.value].default_locale


def parse_game(value: str) -> RiotGame | None:
    """Игра по строковому ключу (значение опции команды) или None."""
    try:
        return RiotGame((value or "").strip().lower())
    except ValueError:
        return None


def game_label(game: RiotGame | str) -> str:
    key = game.value if isinstance(game, RiotGame) else str(game)
    spec = _GAMES.get(key)
    return spec.label if spec is not None else key


def resolve_locale(game: RiotGame, locale: str) -> str:
    """Локаль для игры: серверная настройка, если валидна, иначе дефолт игры.

    Ничего не «знаем» про реальный список локалей сайта — только нормализуем
    строку (нижний регистр, дефис) и откатываемся на дефолт игры, если пусто."""
    normalized = (locale or "").strip().lower().replace("_", "-")
    return normalized or game.default_locale


def news_url(game: RiotGame, locale: str) -> str:
    """URL новостной страницы игры в нужной локали."""
    spec = _GAMES.get(game.value)
    if spec is None:
        raise ValueError(f"нет источника новостей для игры {game.value!r}")
    return spec.news_url.format(locale=resolve_locale(game, locale))
