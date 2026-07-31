from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.moderation.entities import ModCase, TempBan, Warn
from src.domain.moderation.repository import (
    IModCaseRepository,
    ITempBanRepository,
    IWarnRepository,
)
from src.infrastructure.db.models.moderation import ModCaseModel, TempBanModel, WarnModel


class SqlAlchemyWarnRepository(IWarnRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, warn: Warn) -> None:
        self._session.add(
            WarnModel(
                guild_id=warn.guild_id,
                user_id=warn.user_id,
                moderator_id=warn.moderator_id,
                reason=warn.reason,
                created_at=warn.created_at.replace(tzinfo=None),
            )
        )

    async def count(self, user_id: int, guild_id: int, since: datetime | None = None) -> int:
        stmt = (
            select(func.count())
            .select_from(WarnModel)
            .where(WarnModel.user_id == user_id, WarnModel.guild_id == guild_id)
        )
        if since is not None:
            stmt = stmt.where(WarnModel.created_at >= since.replace(tzinfo=None))
        return (await self._session.execute(stmt)).scalar_one()

    async def list(self, user_id: int, guild_id: int) -> list[Warn]:
        stmt = (
            select(WarnModel)
            .where(WarnModel.user_id == user_id, WarnModel.guild_id == guild_id)
            .order_by(WarnModel.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            Warn(
                id=row.id,
                guild_id=row.guild_id,
                user_id=row.user_id,
                moderator_id=row.moderator_id,
                reason=row.reason,
                created_at=row.created_at.replace(tzinfo=UTC),
            )
            for row in rows
        ]

    async def list_guild_counts(self, guild_id: int) -> Sequence[tuple[int, int, datetime]]:
        count = func.count()
        last = func.max(WarnModel.created_at)
        stmt = (
            select(WarnModel.user_id, count, last)
            .where(WarnModel.guild_id == guild_id)
            .group_by(WarnModel.user_id)
            .order_by(count.desc(), last.desc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [(uid, cnt, last_at.replace(tzinfo=UTC)) for uid, cnt, last_at in rows]

    async def clear(self, user_id: int, guild_id: int) -> None:
        await self._session.execute(
            delete(WarnModel).where(WarnModel.user_id == user_id, WarnModel.guild_id == guild_id)
        )


def _ban_to_domain(row: TempBanModel) -> TempBan:
    return TempBan(
        id=row.id,
        guild_id=row.guild_id,
        user_id=row.user_id,
        moderator_id=row.moderator_id,
        reason=row.reason,
        expires_at=row.expires_at.replace(tzinfo=UTC),
    )


class SqlAlchemyTempBanRepository(ITempBanRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, ban: TempBan) -> None:
        self._session.add(
            TempBanModel(
                guild_id=ban.guild_id,
                user_id=ban.user_id,
                moderator_id=ban.moderator_id,
                reason=ban.reason,
                expires_at=ban.expires_at.replace(tzinfo=None),
            )
        )

    async def list_active(self, guild_id: int, now: datetime) -> list[TempBan]:
        stmt = (
            select(TempBanModel)
            .where(
                TempBanModel.guild_id == guild_id,
                TempBanModel.expires_at > now.replace(tzinfo=None),
            )
            .order_by(TempBanModel.expires_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_ban_to_domain(row) for row in rows]

    async def pop_expired(self, now: datetime) -> list[TempBan]:
        naive_now = now.replace(tzinfo=None)
        stmt = select(TempBanModel).where(TempBanModel.expires_at <= naive_now)
        rows = (await self._session.execute(stmt)).scalars().all()
        expired = [_ban_to_domain(row) for row in rows]
        if rows:
            await self._session.execute(
                delete(TempBanModel).where(TempBanModel.expires_at <= naive_now)
            )
        return expired

    async def remove(self, user_id: int, guild_id: int) -> bool:
        result = await self._session.execute(
            delete(TempBanModel).where(
                TempBanModel.user_id == user_id, TempBanModel.guild_id == guild_id
            )
        )
        return result.rowcount > 0


def _case_to_domain(row: ModCaseModel) -> ModCase:
    return ModCase(
        id=row.id,
        guild_id=row.guild_id,
        user_id=row.user_id,
        moderator_id=row.moderator_id,
        action=row.action,
        reason=row.reason,
        duration_minutes=row.duration_minutes,
        source=row.source,
        created_at=row.created_at.replace(tzinfo=UTC),
    )


class SqlAlchemyModCaseRepository(IModCaseRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, case: ModCase) -> ModCase:
        row = ModCaseModel(
            guild_id=case.guild_id,
            user_id=case.user_id,
            moderator_id=case.moderator_id,
            action=case.action,
            reason=case.reason,
            duration_minutes=case.duration_minutes,
            source=case.source,
            created_at=case.created_at.replace(tzinfo=None),
        )
        self._session.add(row)
        await self._session.flush()  # нужен id (эскалация/панель ссылаются на кейс)
        return _case_to_domain(row)

    async def list_for_user(
        self, guild_id: int, user_id: int, limit: int = 50
    ) -> list[ModCase]:
        stmt = (
            select(ModCaseModel)
            .where(ModCaseModel.guild_id == guild_id, ModCaseModel.user_id == user_id)
            .order_by(ModCaseModel.created_at.desc(), ModCaseModel.id.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_case_to_domain(row) for row in rows]

    async def count_for_user(
        self, guild_id: int, user_id: int, actions: Sequence[str]
    ) -> int:
        if not actions:
            return 0
        stmt = (
            select(func.count())
            .select_from(ModCaseModel)
            .where(
                ModCaseModel.guild_id == guild_id,
                ModCaseModel.user_id == user_id,
                ModCaseModel.action.in_(list(actions)),
            )
        )
        return (await self._session.execute(stmt)).scalar_one()
