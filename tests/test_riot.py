"""Use cases модуля Riot: снимок при добавлении, добавление/удаление через
реальный UoW, логика опроса новостей и наблюдаемость недоступной ленты."""

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.application.riot.dto import RiotGameSnapshot, RiotNewsItemDTO, RiotNewsPage
from src.application.riot.interfaces import IRiotNewsClient
from src.application.riot.use_cases import (
    AddTrackedGameUseCase,
    CountTrackedGamesUseCase,
    FetchLatestNewsUseCase,
    GameAlreadyTrackedError,
    GetTrackedGameUseCase,
    ListTrackedGamesUseCase,
    MarkAnnouncedUseCase,
    PollNewsUseCase,
    RemoveTrackedGameUseCase,
)
from src.domain.riot.refs import (
    GAMES,
    RiotGame,
    game_label,
    news_url,
    parse_game,
    resolve_locale,
)

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def news(gid: str, minutes: int, *, kind: str = "weblink") -> RiotNewsItemDTO:
    return RiotNewsItemDTO(
        gid=gid,
        title=f"News {gid}",
        url=f"https://playvalorant.com/{gid}",
        contents="**x**",
        date=NOW + timedelta(minutes=minutes),
        kind=kind,
    )


class FakeRiot(IRiotNewsClient):
    def __init__(self, pages=None):
        self._pages: dict[RiotGame, RiotNewsPage] = pages or {}
        self.calls: list[tuple[RiotGame, str]] = []

    async def get_news(self, game, locale):
        self.calls.append((game, locale))
        return self._pages.get(game, RiotNewsPage(items=[]))


# --- FetchLatestNewsUseCase -------------------------------------------------


async def test_fetch_picks_latest_news():
    client = FakeRiot({RiotGame.VALORANT: RiotNewsPage(items=[news("a", 0), news("b", 30)])})
    snap = await FetchLatestNewsUseCase(client).execute(RiotGame.VALORANT, "ru-ru")
    assert snap is not None
    assert isinstance(snap, RiotGameSnapshot)
    assert snap.name == "Valorant"
    assert snap.latest_news.gid == "b"
    assert client.calls == [(RiotGame.VALORANT, "ru-ru")]


async def test_fetch_none_when_feed_unavailable():
    client = FakeRiot({RiotGame.VALORANT: RiotNewsPage(ok=False, error="HTTP 403")})
    assert await FetchLatestNewsUseCase(client).execute(RiotGame.VALORANT, "ru-ru") is None


# --- add / get / count / remove (реальный UoW) ------------------------------


async def _add(uow_factory, *, game=RiotGame.VALORANT, name="Valorant", baseline=None, guild_id=10):
    return await AddTrackedGameUseCase(uow_factory).execute(
        guild_id=guild_id,
        game=game,
        name=name,
        added_by=1,
        thread_id=777,
        baseline=baseline,
        now=NOW,
    )


async def test_add_get_count(uow_factory):
    await _add(uow_factory, baseline=news("g5", 0))
    got = await GetTrackedGameUseCase(uow_factory).execute(10, "valorant")
    assert got is not None
    assert got.game == "valorant"
    assert got.last_news_gid == "g5"
    assert await CountTrackedGamesUseCase(uow_factory).execute() == 1
    assert len(await ListTrackedGamesUseCase(uow_factory).execute(10)) == 1


async def test_double_add_raises_friendly_error(uow_factory):
    await _add(uow_factory)
    with pytest.raises(GameAlreadyTrackedError):
        await _add(uow_factory)
    # после пойманной гонки в БД остаётся одна запись
    assert await CountTrackedGamesUseCase(uow_factory).execute() == 1


async def test_remove(uow_factory):
    await _add(uow_factory)
    assert await RemoveTrackedGameUseCase(uow_factory).execute(10, "valorant") is True
    assert await GetTrackedGameUseCase(uow_factory).execute(10, "valorant") is None
    assert await RemoveTrackedGameUseCase(uow_factory).execute(10, "valorant") is False


# --- PollNewsUseCase --------------------------------------------------------


async def test_poll_detects_only_newer(uow_factory):
    await _add(uow_factory, baseline=news("a", 0))
    client = FakeRiot({RiotGame.VALORANT: RiotNewsPage(items=[news("a", 0), news("b", 30)])})
    updates = await PollNewsUseCase(uow_factory, client).execute()
    assert len(updates) == 1
    assert [n.gid for n in updates[0].news] == ["b"]
    tracked = await GetTrackedGameUseCase(uow_factory).execute(10, "valorant")
    assert tracked.last_news_gid == "a"  # use case отметку не двигает


async def test_poll_baseline_none_announces_capped(uow_factory):
    await _add(uow_factory, baseline=None)
    items = [news(f"g{i}", i * 10) for i in range(1, 8)]
    client = FakeRiot({RiotGame.VALORANT: RiotNewsPage(items=items)})
    updates = await PollNewsUseCase(uow_factory, client).execute()
    assert [n.gid for n in updates[0].news] == ["g3", "g4", "g5", "g6", "g7"]


async def test_poll_uses_guild_locale(uow_factory):
    await _add(uow_factory)
    client = FakeRiot()
    provider = SimpleNamespace(get=lambda guild_id, key, default: "en-us")
    await PollNewsUseCase(uow_factory, client, settings_provider=provider).execute()
    assert client.calls == [(RiotGame.VALORANT, "en-us")]


async def test_poll_not_ok_skips_and_warns_after_threshold(uow_factory, caplog):
    await _add(uow_factory)
    client = FakeRiot({RiotGame.VALORANT: RiotNewsPage(ok=False, error="HTTP 403")})
    use_case = PollNewsUseCase(uow_factory, client)
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert await use_case.execute() == []
    assert "недоступна" in caplog.text


async def test_mark_announced_use_case(uow_factory):
    await _add(uow_factory, baseline=None)
    tracked = await GetTrackedGameUseCase(uow_factory).execute(10, "valorant")
    later = NOW + timedelta(hours=1)
    await MarkAnnouncedUseCase(uow_factory).execute(tracked.id, "gid-x", later)
    updated = await GetTrackedGameUseCase(uow_factory).execute(10, "valorant")
    assert updated.last_news_gid == "gid-x"
    assert updated.last_news_date == later


# --- домен: игры, URL и локали ----------------------------------------------


def test_all_games_registered():
    assert set(GAMES) == {"valorant", "lol", "tft", "wildrift", "2xko"}
    assert parse_game("LoL") is RiotGame.LOL
    assert parse_game("2XKO") is RiotGame.TWO_XKO
    assert parse_game("unknown") is None
    assert game_label(RiotGame.TFT) == "Teamfight Tactics"


def test_news_url_per_game():
    assert news_url(RiotGame.VALORANT, "ru-ru") == "https://playvalorant.com/ru-ru/news/"
    assert news_url(RiotGame.LOL, "ru-ru") == (
        "https://www.leagueoflegends.com/ru-ru/news/"
    )
    assert news_url(RiotGame.TWO_XKO, "en-us") == "https://2xko.riotgames.com/en-us/news/"


def test_resolve_locale_normalizes_and_falls_back():
    assert resolve_locale(RiotGame.LOL, "RU_RU") == "ru-ru"
    assert resolve_locale(RiotGame.LOL, "") == "ru-ru"
    # пусто у игры без русского -> её дефолт
    assert resolve_locale(RiotGame.TWO_XKO, "") == "en-us"


async def test_poll_multiple_games_of_same_guild(uow_factory):
    await _add(uow_factory, game=RiotGame.VALORANT, name="Valorant")
    await _add(uow_factory, game=RiotGame.LOL, name="League of Legends")
    client = FakeRiot(
        {
            RiotGame.VALORANT: RiotNewsPage(items=[news("v1", 10)]),
            RiotGame.LOL: RiotNewsPage(items=[news("l1", 20)]),
        }
    )
    updates = await PollNewsUseCase(uow_factory, client).execute()
    assert {u.game.game for u in updates} == {"valorant", "lol"}
