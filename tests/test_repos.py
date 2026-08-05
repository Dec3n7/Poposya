"""Use cases модуля GitHub-репозиториев: снимок при добавлении, добавление/
удаление через реальный UoW и логика опроса релизов на фейковом клиенте."""

from datetime import UTC, datetime, timedelta

from src.application.repos.dto import ReleaseDTO, ReleasesPage, RepoInfoDTO
from src.application.repos.interfaces import IGitHubClient
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

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def rel(release_id: int, minutes: int, tag: str = "v1", **over) -> ReleaseDTO:
    base = dict(
        id=release_id,
        tag_name=tag,
        name=tag,
        body="notes",
        html_url=f"https://github.com/x/y/releases/{tag}",
        published_at=NOW + timedelta(minutes=minutes),
        author="bob",
        prerelease=False,
        draft=False,
    )
    base.update(over)
    return ReleaseDTO(**base)


INFO = RepoInfoDTO(owner="psf", name="requests", description="d", stars=1, html_url="https://x")


class FakeGitHub(IGitHubClient):
    """Настраиваемый клиент: repo_info для get_repo, страница релизов на репо."""

    def __init__(self, repo_info=INFO, pages=None):
        self._repo_info = repo_info
        self._pages: dict[tuple[str, str], ReleasesPage] = pages or {}
        self.calls: list[tuple[str, str, str | None]] = []

    async def get_repo(self, owner, name):
        return self._repo_info

    async def list_releases(self, owner, name, etag=None):
        self.calls.append((owner, name, etag))
        return self._pages.get(
            (owner, name), ReleasesPage(releases=[], etag=f"etag-{name}", rate_remaining=100)
        )


# --- FetchRepoUseCase -------------------------------------------------------


async def test_fetch_returns_snapshot_with_newest_release():
    github = FakeGitHub(
        pages={("psf", "requests"): ReleasesPage(releases=[rel(1, 0), rel(2, 30), rel(3, 10)])}
    )
    snap = await FetchRepoUseCase(github).execute("psf", "requests")
    assert snap is not None
    assert snap.info.full_name == "psf/requests"
    assert snap.latest.id == 2  # новейший по времени публикации


async def test_fetch_none_when_repo_missing():
    github = FakeGitHub(repo_info=None)
    assert await FetchRepoUseCase(github).execute("nope", "nope") is None


async def test_fetch_latest_none_without_releases():
    github = FakeGitHub(pages={("psf", "requests"): ReleasesPage(releases=[])})
    snap = await FetchRepoUseCase(github).execute("psf", "requests")
    assert snap.latest is None


# --- add / get / list / count / remove (реальный UoW) -----------------------


async def _add(uow_factory, *, owner="psf", name="requests", baseline=None, guild_id=10):
    return await AddRepoUseCase(uow_factory).execute(
        guild_id=guild_id,
        owner=owner,
        name=name,
        added_by=1,
        thread_id=777,
        baseline=baseline,
        now=NOW,
    )


async def test_add_get_count_list(uow_factory):
    await _add(uow_factory, name="requests", baseline=rel(5, 0))
    await _add(uow_factory, name="black")
    got = await GetRepoUseCase(uow_factory).execute(10, "psf", "requests")
    assert got is not None and got.last_release_id == 5
    assert await CountReposUseCase(uow_factory).execute() == 2
    assert len(await ListReposUseCase(uow_factory).execute(10)) == 2


async def test_remove(uow_factory):
    await _add(uow_factory)
    assert await RemoveRepoUseCase(uow_factory).execute(10, "psf", "requests") is True
    assert await GetRepoUseCase(uow_factory).execute(10, "psf", "requests") is None
    assert await RemoveRepoUseCase(uow_factory).execute(10, "psf", "requests") is False


async def test_mark_announced_use_case(uow_factory):
    await _add(uow_factory, baseline=None)
    repo = await GetRepoUseCase(uow_factory).execute(10, "psf", "requests")
    later = NOW + timedelta(hours=2)
    await MarkAnnouncedUseCase(uow_factory).execute(repo.id, 88, later, "e1")
    updated = await GetRepoUseCase(uow_factory).execute(10, "psf", "requests")
    assert updated.last_release_id == 88
    assert updated.last_published_at == later
    assert updated.etag == "e1"


# --- PollReleasesUseCase ----------------------------------------------------


async def test_poll_detects_only_newer_than_baseline(uow_factory):
    await _add(uow_factory, baseline=rel(1, 0))  # отметка = релиз id=1 в NOW
    github = FakeGitHub(
        pages={("psf", "requests"): ReleasesPage(releases=[rel(1, 0), rel(2, 30)], etag="fresh")}
    )
    updates = await PollReleasesUseCase(uow_factory, github).execute()
    assert len(updates) == 1
    assert [r.id for r in updates[0].new_releases] == [2]
    assert updates[0].etag == "fresh"
    # отметку сам use case не двигает — это делает ког после отправки
    repo = await GetRepoUseCase(uow_factory).execute(10, "psf", "requests")
    assert repo.last_release_id == 1


async def test_poll_skips_drafts(uow_factory):
    await _add(uow_factory, baseline=rel(1, 0))
    github = FakeGitHub(
        pages={("psf", "requests"): ReleasesPage(releases=[rel(2, 30, draft=True), rel(3, 40)])}
    )
    updates = await PollReleasesUseCase(uow_factory, github).execute()
    assert [r.id for r in updates[0].new_releases] == [3]


async def test_poll_no_new_saves_etag(uow_factory):
    await _add(uow_factory, baseline=rel(2, 30))  # отметка на новейшем
    github = FakeGitHub(
        pages={("psf", "requests"): ReleasesPage(releases=[rel(1, 0), rel(2, 30)], etag="e-saved")}
    )
    updates = await PollReleasesUseCase(uow_factory, github).execute()
    assert updates == []
    repo = await GetRepoUseCase(uow_factory).execute(10, "psf", "requests")
    assert repo.etag == "e-saved"


async def test_poll_not_modified_changes_nothing(uow_factory):
    await _add(uow_factory, baseline=rel(2, 30))
    # предзаполним etag, чтобы увидеть, что 304 его не трогает
    repo = await GetRepoUseCase(uow_factory).execute(10, "psf", "requests")
    await MarkAnnouncedUseCase(uow_factory).execute(repo.id, 2, NOW + timedelta(minutes=30), "orig")
    github = FakeGitHub(
        pages={("psf", "requests"): ReleasesPage(not_modified=True, etag="ignored")}
    )
    updates = await PollReleasesUseCase(uow_factory, github).execute()
    assert updates == []
    again = await GetRepoUseCase(uow_factory).execute(10, "psf", "requests")
    assert again.etag == "orig"


async def test_poll_baseline_none_announces_newest_capped(uow_factory):
    await _add(uow_factory, baseline=None)
    releases = [rel(i, i * 10, tag=f"v{i}") for i in range(1, 8)]  # 7 релизов
    github = FakeGitHub(pages={("psf", "requests"): ReleasesPage(releases=releases)})
    updates = await PollReleasesUseCase(uow_factory, github).execute()
    ids = [r.id for r in updates[0].new_releases]
    assert ids == [3, 4, 5, 6, 7]  # новейшие пять, от старого к новому


async def test_poll_stops_when_rate_low(uow_factory):
    await _add(uow_factory, owner="psf", name="a")
    await _add(uow_factory, owner="psf", name="b")
    github = FakeGitHub(
        pages={
            ("psf", "a"): ReleasesPage(releases=[], etag="ea", rate_remaining=0),
            ("psf", "b"): ReleasesPage(releases=[], etag="eb", rate_remaining=100),
        }
    )
    await PollReleasesUseCase(uow_factory, github, rate_safety_floor=5).execute()
    # после первого репо остаток 0 < 5 → второй не опрашивается
    assert len(github.calls) == 1
