from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.banwatch.entities import ServerBan
from src.domain.banwatch.repository import IServerBanRepository
from src.infrastructure.db.models.banwatch import ServerBanModel


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _naive(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=None) if value is not None else None


def _to_domain(row: ServerBanModel) -> ServerBan:
    return ServerBan(
        id=row.id,
        user_id=row.user_id,
        guild_id=row.guild_id,
        guild_name=row.guild_name,
        reason=row.reason,
        banned_at=_aware(row.banned_at),
    )


class SqlAlchemyServerBanRepository(IServerBanRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert(self, ban: ServerBan) -> None:
        stmt = (
            select(ServerBanModel)
            .where(
                ServerBanModel.guild_id == ban.guild_id,
                ServerBanModel.user_id == ban.user_id,
            )
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            self._session.add(
                ServerBanModel(
                    user_id=ban.user_id,
                    guild_id=ban.guild_id,
                    guild_name=ban.guild_name,
                    reason=ban.reason,
                    banned_at=_naive(ban.banned_at),
                )
            )
        else:
            row.guild_name = ban.guild_name
            row.reason = ban.reason
            row.banned_at = _naive(ban.banned_at)

    async def remove(self, guild_id: int, user_id: int) -> None:
        await self._session.execute(
            delete(ServerBanModel).where(
                ServerBanModel.guild_id == guild_id,
                ServerBanModel.user_id == user_id,
            )
        )

    async def list_for_user(self, user_id: int) -> list[ServerBan]:
        stmt = select(ServerBanModel).where(ServerBanModel.user_id == user_id)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(row) for row in rows]

    async def replace_guild(self, guild_id: int, bans: list[ServerBan]) -> None:
        await self._session.execute(
            delete(ServerBanModel).where(ServerBanModel.guild_id == guild_id)
        )
        for ban in bans:
            self._session.add(
                ServerBanModel(
                    user_id=ban.user_id,
                    guild_id=ban.guild_id,
                    guild_name=ban.guild_name,
                    reason=ban.reason,
                    banned_at=_naive(ban.banned_at),
                )
            )

    async def flagged_candidates(
        self, exclude_guild_id: int, threshold: int
    ) -> list[tuple[int, int]]:
        # (guild_id, user_id) уникальна → count строк = число серверов на юзера
        count = func.count()
        stmt = (
            select(ServerBanModel.user_id, count)
            .where(ServerBanModel.guild_id != exclude_guild_id)
            .group_by(ServerBanModel.user_id)
            .having(count >= threshold)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(user_id, total) for user_id, total in rows]
