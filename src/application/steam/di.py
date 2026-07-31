from dataclasses import dataclass

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


@dataclass(frozen=True)
class SteamContainer:
    """Зависимости модуля «Steam-игры»; собирается в root_container."""

    fetch_game: FetchGameUseCase
    get_game: GetGameUseCase
    list_games: ListGamesUseCase
    count_games: CountGamesUseCase
    add_game: AddGameUseCase
    remove_game: RemoveGameUseCase
    mark_announced: MarkAnnouncedUseCase
    poll_news: PollNewsUseCase
