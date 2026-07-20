from datetime import UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.audit.entities import AuditEntry
from src.domain.audit.repository import IAuditRepository
from src.infrastructure.db.models.audit import PanelAuditModel


class SqlAlchemyAuditRepository(IAuditRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, entry: AuditEntry) -> None:
        assert entry.created_at is not None
        self._session.add(
            PanelAuditModel(
                guild_id=entry.guild_id,
                actor_id=entry.actor_id,
                action=entry.action,
                target=entry.target,
                details=entry.details,
                result=entry.result,
                created_at=entry.created_at.replace(tzinfo=None),
            )
        )

    async def list_for_guild(self, guild_id: int, limit: int) -> list[AuditEntry]:
        stmt = (
            select(PanelAuditModel)
            .where(PanelAuditModel.guild_id == guild_id)
            .order_by(PanelAuditModel.id.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            AuditEntry(
                id=row.id,
                guild_id=row.guild_id,
                actor_id=row.actor_id,
                action=row.action,
                target=row.target,
                details=row.details,
                result=row.result,
                created_at=row.created_at.replace(tzinfo=UTC),
            )
            for row in rows
        ]
