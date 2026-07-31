import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.application.repos.dto import ReleaseDTO, RepoSnapshot
from src.application.repos.interfaces import IGitHubClient
from src.domain.repos.entities import TrackedRepo

logger = logging.getLogger(__name__)

UowFactory = Callable[[], IUnitOfWork]

# Сколько релизов максимум разослать за один опрос одного репозитория: если у
# репо без релизов вдруг появилось сразу много, не заливаем тред десятками
# сообщений — берём новейшие.
_MAX_ANNOUNCE_PER_CYCLE = 5


class FetchRepoUseCase:
    """Снимок репозитория из GitHub: карточка + самый свежий релиз. Сетевой
    запрос без записи в БД — вызывается на /git add до создания треда."""

    def __init__(self, github: IGitHubClient):
        self._github = github

    async def execute(self, owner: str, name: str) -> RepoSnapshot | None:
        info = await self._github.get_repo(owner, name)
        if info is None:
            return None
        page = await self._github.list_releases(owner, name)
        latest: ReleaseDTO | None = None
        if page.ok:
            published = [r for r in page.releases if not r.draft]
            if published:
                latest = max(published, key=lambda r: r.marker)
        return RepoSnapshot(info=info, latest=latest)


class GetRepoUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, owner: str, name: str) -> TrackedRepo | None:
        async with self._uow_factory() as uow:
            return await uow.tracked_repos.get(guild_id, owner, name)


class ListReposUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int) -> list[TrackedRepo]:
        async with self._uow_factory() as uow:
            return await uow.tracked_repos.list_for_guild(guild_id)


class CountReposUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self) -> int:
        async with self._uow_factory() as uow:
            return await uow.tracked_repos.count_all()


class AddRepoUseCase:
    """Сохраняет репозиторий с уже созданным тредом и «отметкой» текущего
    последнего релиза (чтобы не объявлять то, что вышло до добавления)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self,
        guild_id: int,
        owner: str,
        name: str,
        added_by: int,
        thread_id: int,
        baseline: ReleaseDTO | None,
        now: datetime,
    ) -> TrackedRepo:
        repo = TrackedRepo(
            guild_id=guild_id,
            owner=owner,
            name=name,
            thread_id=thread_id,
            last_release_id=baseline.id if baseline else 0,
            last_published_at=baseline.published_at if baseline else None,
            added_by=added_by,
            created_at=now,
        )
        async with self._uow_factory() as uow:
            saved = await uow.tracked_repos.add(repo)
            await uow.commit()
        logger.info(
            "Репозиторий добавлен в отслеживание",
            extra={"guild_id": guild_id, "repo": repo.full_name},
        )
        return saved


class RemoveRepoUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, owner: str, name: str) -> bool:
        async with self._uow_factory() as uow:
            removed = await uow.tracked_repos.remove(guild_id, owner, name)
            await uow.commit()
        return removed


class MarkAnnouncedUseCase:
    """Сдвиг отметки после успешной отправки релиза в тред."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self, repo_id: int, release_id: int, published_at: datetime, etag: str | None
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.tracked_repos.mark_announced(repo_id, release_id, published_at, etag)
            await uow.commit()


@dataclass(frozen=True)
class RepoUpdate:
    """Найденные новые релизы репозитория, от старого к новому. etag ответа
    сохраняется тредом только после отправки последнего релиза."""

    repo: TrackedRepo
    new_releases: list[ReleaseDTO] = field(default_factory=list)
    etag: str | None = None


class PollReleasesUseCase:
    """Один такт опроса: для каждого репозитория спрашивает GitHub список
    релизов (условным запросом по etag) и отбирает те, что новее отметки.

    Состояние не сдвигает: для репозиториев с новыми релизами возвращает их
    когу (тот объявит и подвинет отметку сам), для «без изменений» освежает
    только etag. Сеть дёргается вне транзакции — БД-сессии короткие."""

    def __init__(
        self,
        uow_factory: UowFactory,
        github: IGitHubClient,
        rate_safety_floor: int = 5,
    ):
        self._uow_factory = uow_factory
        self._github = github
        self._floor = rate_safety_floor

    async def execute(self) -> list[RepoUpdate]:
        async with self._uow_factory() as uow:
            repos = await uow.tracked_repos.list_all()
        if not repos:
            return []

        updates: list[RepoUpdate] = []
        etag_saves: list[tuple[int, str]] = []
        remaining: int | None = None

        for repo in repos:
            if remaining is not None and remaining < self._floor:
                logger.warning(
                    "Опрос релизов прерван: лимит GitHub на исходе",
                    extra={"remaining": remaining},
                )
                break
            page = await self._github.list_releases(repo.owner, repo.name, repo.etag)
            if page.rate_remaining is not None:
                remaining = page.rate_remaining
            if not page.ok or page.not_modified:
                continue
            baseline = repo.marker()
            fresh = sorted(
                (r for r in page.releases if not r.draft and r.marker > baseline),
                key=lambda r: r.marker,
            )
            if not fresh:
                if page.etag and page.etag != repo.etag and repo.id is not None:
                    etag_saves.append((repo.id, page.etag))
                continue
            updates.append(RepoUpdate(repo, fresh[-_MAX_ANNOUNCE_PER_CYCLE:], page.etag))

        if etag_saves:
            async with self._uow_factory() as uow:
                for repo_id, etag in etag_saves:
                    await uow.tracked_repos.save_etag(repo_id, etag)
                await uow.commit()

        return updates
