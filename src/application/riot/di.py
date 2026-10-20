from dataclasses import dataclass

from src.application.riot.use_cases import (
    AddTrackedGameUseCase,
    CountTrackedGamesUseCase,
    FetchLatestNewsUseCase,
    GetTrackedGameUseCase,
    ListTrackedGamesUseCase,
    MarkAnnouncedUseCase,
    PollNewsUseCase,
    RemoveTrackedGameUseCase,
)


@dataclass(frozen=True)
class RiotContainer:
    """Зависимости модуля «Riot-игры»; собирается в root_container."""

    fetch_news: FetchLatestNewsUseCase
    get_game: GetTrackedGameUseCase
    list_games: ListTrackedGamesUseCase
    count_games: CountTrackedGamesUseCase
    add_game: AddTrackedGameUseCase
    remove_game: RemoveTrackedGameUseCase
    mark_announced: MarkAnnouncedUseCase
    poll_news: PollNewsUseCase
