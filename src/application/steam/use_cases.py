import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.application.steam.dto import GameSnapshot, NewsItemDTO
from src.application.steam.interfaces import ISteamClient
from src.domain.steam.entities import TrackedGame

logger = logging.getLogger(__name__)

UowFactory = Callable[[], IUnitOfWork]

# Сколько новостей максимум разослать за один опрос одной игры — чтобы игра с
# внезапно длинной лентой не залила тред десятками сообщений.
_MAX_ANNOUNCE_PER_CYCLE = 5


def _latest_official(items: list[NewsItemDTO]) -> NewsItemDTO | None:
    official = [n for n in items if n.is_official]
    return max(official, key=lambda n: n.marker) if official else None


class FetchGameUseCase:
    """Снимок игры из Steam: карточка + самая свежая официальная новость.
    Сетевой запрос без записи в БД — вызывается на /steam add до создания треда."""

    def __init__(self, steam: ISteamClient):
        self._steam = steam

    async def execute(self, appid: int) -> GameSnapshot | None:
        info = await self._steam.get_game(appid)
        if info is None:
            return None
        page = await self._steam.get_news(appid)
        latest = _latest_official(page.items) if page.ok else None
        return GameSnapshot(info=info, latest_news=latest)


class GetGameUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, appid: int) -> TrackedGame | None:
        async with self._uow_factory() as uow:
            return await uow.tracked_games.get(guild_id, appid)


class ListGamesUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int) -> list[TrackedGame]:
        async with self._uow_factory() as uow:
            return await uow.tracked_games.list_for_guild(guild_id)


class CountGamesUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self) -> int:
        async with self._uow_factory() as uow:
            return await uow.tracked_games.count_all()


class AddGameUseCase:
    """Сохраняет игру с уже созданным тредом и «отметкой» текущей последней
    новости (чтобы не объявлять то, что вышло до добавления)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self,
        guild_id: int,
        appid: int,
        name: str,
        added_by: int,
        thread_id: int,
        baseline: NewsItemDTO | None,
        now: datetime,
    ) -> TrackedGame:
        game = TrackedGame(
            guild_id=guild_id,
            appid=appid,
            name=name,
            thread_id=thread_id,
            last_news_gid=baseline.gid if baseline else "",
            last_news_date=baseline.date if baseline else None,
            added_by=added_by,
            created_at=now,
        )
        async with self._uow_factory() as uow:
            saved = await uow.tracked_games.add(game)
            await uow.commit()
        logger.info(
            "Игра Steam добавлена в отслеживание",
            # ключ "name" зарезервирован в LogRecord — используем "game"
            extra={"guild_id": guild_id, "appid": appid, "game": name},
        )
        return saved


class RemoveGameUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, appid: int) -> bool:
        async with self._uow_factory() as uow:
            removed = await uow.tracked_games.remove(guild_id, appid)
            await uow.commit()
        return removed


class MarkAnnouncedUseCase:
    """Сдвиг отметки после успешной отправки новости в тред."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, game_id: int, gid: str, date: datetime) -> None:
        async with self._uow_factory() as uow:
            await uow.tracked_games.mark_announced(game_id, gid, date)
            await uow.commit()


@dataclass(frozen=True)
class GameUpdate:
    """Найденные новые официальные новости игры, от старой к новой."""

    game: TrackedGame
    news: list[NewsItemDTO] = field(default_factory=list)


class PollNewsUseCase:
    """Один такт опроса: для каждой игры спрашивает Steam ленту новостей и
    отбирает официальные новее отметки. Состояние не сдвигает — это делает ког
    после отправки. Сеть дёргается вне транзакции — БД-сессии короткие."""

    def __init__(self, uow_factory: UowFactory, steam: ISteamClient):
        self._uow_factory = uow_factory
        self._steam = steam

    async def execute(self) -> list[GameUpdate]:
        async with self._uow_factory() as uow:
            games = await uow.tracked_games.list_all()
        if not games:
            return []

        updates: list[GameUpdate] = []
        for game in games:
            page = await self._steam.get_news(game.appid)
            if not page.ok:
                continue
            baseline = game.marker()
            fresh = sorted(
                (n for n in page.items if n.is_official and n.marker > baseline),
                key=lambda n: n.marker,
            )
            if fresh:
                updates.append(GameUpdate(game, fresh[-_MAX_ANNOUNCE_PER_CYCLE:]))
        return updates
