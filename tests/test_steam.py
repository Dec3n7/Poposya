"""Use cases модуля Steam: снимок при добавлении, добавление/удаление через
реальный UoW и логика опроса новостей (только официальные) на фейковом клиенте."""

import logging
from datetime import UTC, datetime, timedelta

from src.application.steam.dto import GameInfoDTO, NewsItemDTO, NewsPage
from src.application.steam.interfaces import ISteamClient
from src.application.steam.use_cases import (
    AddGameUseCase,
    CountGamesUseCase,
    FetchGameUseCase,
    GetGameUseCase,
    ListGamesUseCase,
    MarkAnnouncedUseCase,
    PollNewsUseCase,
    RemoveGameUseCase,
)

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
INFO = GameInfoDTO(appid=730, name="CS2", short_description="d", header_image="h", store_url="u")


def news(gid: str, minutes: int, *, external: bool = False) -> NewsItemDTO:
    return NewsItemDTO(
        gid=gid,
        title=f"News {gid}",
        url="https://steam/n",
        contents="[b]x[/b]",
        feedname="steam_community_announcements" if not external else "PC Gamer",
        feedlabel="Community Announcements",
        date=NOW + timedelta(minutes=minutes),
        is_external_url=external,
    )


class FakeSteam(ISteamClient):
    def __init__(self, info=INFO, pages=None):
        self._info = info
        self._pages: dict[int, NewsPage] = pages or {}
        self.calls: list[int] = []

    async def get_game(self, appid):
        return self._info

    async def get_news(self, appid):
        self.calls.append(appid)
        return self._pages.get(appid, NewsPage(items=[]))


# --- FetchGameUseCase -------------------------------------------------------


async def test_fetch_picks_latest_official():
    steam = FakeSteam(
        pages={730: NewsPage(items=[news("a", 0), news("ext", 99, external=True), news("b", 30)])}
    )
    snap = await FetchGameUseCase(steam).execute(730)
    assert snap is not None
    assert snap.info.name == "CS2"
    assert snap.latest_news.gid == "b"  # новейшая ОФИЦИАЛЬНАЯ (external игнор)


async def test_fetch_none_when_missing():
    assert await FetchGameUseCase(FakeSteam(info=None)).execute(1) is None


async def test_fetch_latest_none_without_official():
    steam = FakeSteam(pages={730: NewsPage(items=[news("ext", 5, external=True)])})
    snap = await FetchGameUseCase(steam).execute(730)
    assert snap.latest_news is None


# --- add / get / count / remove (реальный UoW) ------------------------------


async def _add(uow_factory, *, appid=730, name="CS2", baseline=None, guild_id=10):
    return await AddGameUseCase(uow_factory).execute(
        guild_id=guild_id,
        appid=appid,
        name=name,
        added_by=1,
        thread_id=777,
        baseline=baseline,
        now=NOW,
    )


async def test_add_get_count(uow_factory):
    await _add(uow_factory, appid=730, name="CS2", baseline=news("g5", 0))
    await _add(uow_factory, appid=440, name="TF2")
    got = await GetGameUseCase(uow_factory).execute(10, 730)
    assert got is not None and got.last_news_gid == "g5"
    assert await CountGamesUseCase(uow_factory).execute() == 2
    assert len(await ListGamesUseCase(uow_factory).execute(10)) == 2


async def test_add_logs_safely_at_info(uow_factory, caplog):
    # ключ "name" в extra зарезервирован LogRecord — под INFO лог не должен падать
    with caplog.at_level(logging.INFO):
        await _add(uow_factory, name="CS2")
    assert await GetGameUseCase(uow_factory).execute(10, 730) is not None


async def test_remove(uow_factory):
    await _add(uow_factory)
    assert await RemoveGameUseCase(uow_factory).execute(10, 730) is True
    assert await GetGameUseCase(uow_factory).execute(10, 730) is None
    assert await RemoveGameUseCase(uow_factory).execute(10, 730) is False


# --- PollNewsUseCase --------------------------------------------------------


async def test_poll_detects_only_newer_official(uow_factory):
    await _add(uow_factory, baseline=news("a", 0))  # отметка = новость a в NOW
    steam = FakeSteam(
        pages={730: NewsPage(items=[news("a", 0), news("ext", 40, external=True), news("b", 30)])}
    )
    updates = await PollNewsUseCase(uow_factory, steam).execute()
    assert len(updates) == 1
    # только b: a не новее отметки, ext — внешняя
    assert [n.gid for n in updates[0].news] == ["b"]
    game = await GetGameUseCase(uow_factory).execute(10, 730)
    assert game.last_news_gid == "a"  # use case отметку не двигает


async def test_poll_baseline_none_announces_capped(uow_factory):
    await _add(uow_factory, baseline=None)
    items = [news(f"g{i}", i * 10) for i in range(1, 8)]  # 7 официальных
    steam = FakeSteam(pages={730: NewsPage(items=items)})
    updates = await PollNewsUseCase(uow_factory, steam).execute()
    ids = [n.gid for n in updates[0].news]
    assert ids == ["g3", "g4", "g5", "g6", "g7"]  # новейшие пять, от старой к новой


async def test_poll_not_ok_skips(uow_factory):
    await _add(uow_factory, baseline=news("a", 0))
    steam = FakeSteam(pages={730: NewsPage(ok=False)})
    assert await PollNewsUseCase(uow_factory, steam).execute() == []


async def test_mark_announced_use_case(uow_factory):
    await _add(uow_factory, baseline=None)
    game = await GetGameUseCase(uow_factory).execute(10, 730)
    later = NOW + timedelta(hours=1)
    await MarkAnnouncedUseCase(uow_factory).execute(game.id, "gid-x", later)
    updated = await GetGameUseCase(uow_factory).execute(10, 730)
    assert updated.last_news_gid == "gid-x"
    assert updated.last_news_date == later
