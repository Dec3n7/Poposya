from dataclasses import dataclass

from src.application.repos.use_cases import (
    AddRepoUseCase,
    CountReposUseCase,
    FetchRepoUseCase,
    GetRepoUseCase,
    ListReposUseCase,
    MarkAnnouncedUseCase,
    PollReleasesUseCase,
    RemoveRepoUseCase,
)


@dataclass(frozen=True)
class ReposContainer:
    """Зависимости модуля «GitHub-репозитории»; собирается в root_container."""

    fetch_repo: FetchRepoUseCase
    get_repo: GetRepoUseCase
    list_repos: ListReposUseCase
    count_repos: CountReposUseCase
    add_repo: AddRepoUseCase
    remove_repo: RemoveRepoUseCase
    mark_announced: MarkAnnouncedUseCase
    poll_releases: PollReleasesUseCase
