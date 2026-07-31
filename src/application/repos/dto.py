from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ReleaseDTO:
    """Релиз GitHub в терминах приложения (без формата ответа API)."""

    id: int
    tag_name: str
    name: str
    body: str
    html_url: str
    published_at: datetime
    author: str = ""
    prerelease: bool = False
    draft: bool = False

    @property
    def marker(self) -> tuple[datetime, int]:
        return (self.published_at, self.id)


@dataclass(frozen=True)
class RepoInfoDTO:
    """Карточка репозитория для шапки треда."""

    owner: str
    name: str
    description: str = ""
    stars: int = 0
    language: str = ""
    html_url: str = ""
    default_branch: str = "main"

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class ReleasesPage:
    """Результат запроса списка релизов с учётом условного запроса.

    not_modified — GitHub ответил 304 (тело не менялось с прошлого ETag).
    ok=False — сеть/ошибка сервера: состояние трогать нельзя, повторим позже."""

    releases: list[ReleaseDTO] = field(default_factory=list)
    etag: str | None = None
    not_modified: bool = False
    ok: bool = True
    rate_remaining: int | None = None


@dataclass(frozen=True)
class RepoSnapshot:
    """Снимок репозитория при добавлении: карточка + самый свежий релиз (если
    вообще есть). latest = None означает «релизов нет — объявлять пока нечего»."""

    info: RepoInfoDTO
    latest: ReleaseDTO | None
