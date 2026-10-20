import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from src.application.interfaces.settings_provider import ISettingsProvider
from src.application.interfaces.unit_of_work import IUnitOfWork
from src.application.riot.dto import RiotGameSnapshot, RiotNewsItemDTO, RiotNewsPage
from src.application.riot.interfaces import IRiotNewsClient
from src.domain.riot.entities import TrackedRiotGame
from src.domain.riot.refs import DEFAULT_LOCALE, RiotGame, game_label, parse_game

logger = logging.getLogger(__name__)

UowFactory = Callable[[], IUnitOfWork]

# Сколько новостей максимум разослать за один опрос одной игры — чтобы лента с
# внезапно длинным хвостом не залила тред десятками сообщений.
_MAX_ANNOUNCE_PER_CYCLE = 5

# После стольких неудачных тактов подряд пишем warning: «тишина» при geo- или
# контрактном отказе должна быть заметна оператору, а не длиться вечно молча.
_FAILURE_WARN_THRESHOLD = 3


class GameAlreadyTrackedError(Exception):
    """Игра уже отслеживается (в т.ч. гонка двух одновременных /riot add)."""


def _latest(items: list[RiotNewsItemDTO]) -> RiotNewsItemDTO | None:
    return max(items, key=lambda n: n.marker) if items else None


class FetchLatestNewsUseCase:
    """Снимок игры до создания треда: подпись + самая свежая новость.
    Сетевой запрос без записи в БД."""

    def __init__(self, client: IRiotNewsClient):
        self._client = client

    async def execute(self, game: RiotGame, locale: str) -> RiotGameSnapshot | None:
        page = await self._client.get_news(game, locale)
        if not page.ok:
            return None
        return RiotGameSnapshot(
            game=game.value, name=game_label(game), latest_news=_latest(page.items)
        )


class GetTrackedGameUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, game: str) -> TrackedRiotGame | None:
        async with self._uow_factory() as uow:
            return await uow.riot_games.get(guild_id, game)


class ListTrackedGamesUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int) -> list[TrackedRiotGame]:
        async with self._uow_factory() as uow:
            return await uow.riot_games.list_for_guild(guild_id)


class CountTrackedGamesUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self) -> int:
        async with self._uow_factory() as uow:
            return await uow.riot_games.count_all()


class AddTrackedGameUseCase:
    """Сохраняет игру с уже созданным тредом и «отметкой» текущей последней
    новости (чтобы не объявлять то, что вышло до добавления)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self,
        guild_id: int,
        game: RiotGame,
        name: str,
        added_by: int,
        thread_id: int,
        baseline: RiotNewsItemDTO | None,
        now: datetime,
    ) -> TrackedRiotGame:
        tracked = TrackedRiotGame(
            guild_id=guild_id,
            game=game.value,
            name=name,
            thread_id=thread_id,
            last_news_gid=baseline.gid if baseline else "",
            last_news_date=baseline.date if baseline else None,
            added_by=added_by,
            created_at=now,
        )
        async with self._uow_factory() as uow:
            try:
                saved = await uow.riot_games.add(tracked)
                await uow.commit()
            except IntegrityError as exc:
                await uow.rollback()
                raise GameAlreadyTrackedError(game.value) from exc
        logger.info(
            "Игра Riot добавлена в отслеживание",
            extra={"guild_id": guild_id, "game": game.value},
        )
        return saved


class RemoveTrackedGameUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, game: str) -> bool:
        async with self._uow_factory() as uow:
            removed = await uow.riot_games.remove(guild_id, game)
            await uow.commit()
        return removed


class MarkAnnouncedUseCase:
    """Сдвиг отметки после успешной отправки новости в тред."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, game_id: int, gid: str, date: datetime) -> None:
        async with self._uow_factory() as uow:
            await uow.riot_games.mark_announced(game_id, gid, date)
            await uow.commit()


@dataclass(frozen=True)
class RiotGameUpdate:
    """Найденные новые новости игры, от старой к новой."""

    game: TrackedRiotGame
    news: list[RiotNewsItemDTO] = field(default_factory=list)


class PollNewsUseCase:
    """Один такт опроса: для каждой игры спрашивает ленту и отбирает новости
    новее отметки. Состояние не сдвигает — это делает ког после отправки.
    Сеть дёргается вне транзакции — БД-сессии короткие."""

    def __init__(
        self,
        uow_factory: UowFactory,
        client: IRiotNewsClient,
        settings_provider: ISettingsProvider | None = None,
        default_locale: str = DEFAULT_LOCALE,
    ):
        self._uow_factory = uow_factory
        self._client = client
        self._settings = settings_provider
        self._default_locale = default_locale
        self._failures: dict[tuple[int, str], int] = {}

    def _locale(self, guild_id: int) -> str:
        if self._settings is None:
            return self._default_locale
        value = self._settings.get(guild_id, "riot_locale", self._default_locale)
        return str(value or self._default_locale)

    def _note_failure(self, guild_id: int, game: str, page: RiotNewsPage) -> None:
        key = (guild_id, game)
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        if count >= _FAILURE_WARN_THRESHOLD:
            logger.warning(
                "Лента Riot недоступна %d тактов подряд — проверьте geo/Cloudflare",
                count,
                extra={"guild_id": guild_id, "game": game, "error": page.error},
            )

    async def execute(self) -> list[RiotGameUpdate]:
        async with self._uow_factory() as uow:
            games = await uow.riot_games.list_all()
        if not games:
            return []

        updates: list[RiotGameUpdate] = []
        for tracked in games:
            game = parse_game(tracked.game)
            if game is None:
                logger.warning(
                    "Неизвестная игра Riot в отслеживании — пропускаю",
                    extra={"guild_id": tracked.guild_id, "game": tracked.game},
                )
                continue
            page = await self._client.get_news(game, self._locale(tracked.guild_id))
            if not page.ok:
                self._note_failure(tracked.guild_id, tracked.game, page)
                continue
            self._failures.pop((tracked.guild_id, tracked.game), None)
            baseline = tracked.marker()
            fresh = sorted(
                (n for n in page.items if n.marker > baseline),
                key=lambda n: n.marker,
            )
            if fresh:
                updates.append(RiotGameUpdate(tracked, fresh[-_MAX_ANNOUNCE_PER_CYCLE:]))
        return updates
