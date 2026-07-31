from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repos.entities import TrackedRepo
from src.domain.repos.repository import ITrackedRepoRepository
from src.infrastructure.db.models.repos import TrackedRepoModel


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _naive(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=None) if value is not None else None


def _to_domain(row: TrackedRepoModel) -> TrackedRepo:
    return TrackedRepo(
        id=row.id,
        guild_id=row.guild_id,
        owner=row.owner,
        name=row.name,
        thread_id=row.thread_id,
        last_release_id=row.last_release_id,
        last_published_at=_aware(row.last_published_at),
        etag=row.etag,
        added_by=row.added_by,
        created_at=_aware(row.created_at),
    )


class SqlAlchemyTrackedRepoRepository(ITrackedRepoRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, repo: TrackedRepo) -> TrackedRepo:
        row = TrackedRepoModel(
            guild_id=repo.guild_id,
            owner=repo.owner,
            name=repo.name,
            thread_id=repo.thread_id,
            last_release_id=repo.last_release_id,
            last_published_at=_naive(repo.last_published_at),
            etag=repo.etag,
            added_by=repo.added_by,
            created_at=_naive(repo.created_at) or datetime.now(UTC).replace(tzinfo=None),
        )
        self._session.add(row)
        await self._session.flush()  # получить id
        repo.id = row.id
        return repo

    async def get(self, guild_id: int, owner: str, name: str) -> TrackedRepo | None:
        stmt = (
            select(TrackedRepoModel)
            .where(
                TrackedRepoModel.guild_id == guild_id,
                TrackedRepoModel.owner == owner,
                TrackedRepoModel.name == name,
            )
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(row) if row else None

    async def list_for_guild(self, guild_id: int) -> list[TrackedRepo]:
        stmt = (
            select(TrackedRepoModel)
            .where(TrackedRepoModel.guild_id == guild_id)
            .order_by(TrackedRepoModel.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(row) for row in rows]

    async def list_all(self) -> list[TrackedRepo]:
        stmt = select(TrackedRepoModel).order_by(TrackedRepoModel.id)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(row) for row in rows]

    async def count_all(self) -> int:
        stmt = select(func.count()).select_from(TrackedRepoModel)
        return int((await self._session.execute(stmt)).scalar_one())

    async def remove(self, guild_id: int, owner: str, name: str) -> bool:
        result = await self._session.execute(
            delete(TrackedRepoModel).where(
                TrackedRepoModel.guild_id == guild_id,
                TrackedRepoModel.owner == owner,
                TrackedRepoModel.name == name,
            )
        )
        return result.rowcount > 0

    async def mark_announced(
        self, repo_id: int, release_id: int, published_at: datetime, etag: str | None
    ) -> None:
        row = await self._session.get(TrackedRepoModel, repo_id)
        if row is None:
            return
        row.last_release_id = release_id
        row.last_published_at = _naive(published_at)
        if etag is not None:
            row.etag = etag

    async def save_etag(self, repo_id: int, etag: str) -> None:
        row = await self._session.get(TrackedRepoModel, repo_id)
        if row is not None:
            row.etag = etag
