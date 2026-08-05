from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.appeals.entities import STATUS_PENDING, Appeal
from src.domain.appeals.repository import IAppealRepository
from src.infrastructure.db.models.appeals import AppealModel


def _to_domain(row: AppealModel) -> Appeal:
    return Appeal(
        id=row.id,
        guild_id=row.guild_id,
        user_id=row.user_id,
        action=row.action,
        text=row.text,
        original_reason=row.original_reason,
        status=row.status,
        review_message_id=row.review_message_id,
        resolver_id=row.resolver_id,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
    )


class SqlAlchemyAppealRepository(IAppealRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, appeal: Appeal) -> Appeal:
        row = AppealModel(
            guild_id=appeal.guild_id,
            user_id=appeal.user_id,
            action=appeal.action,
            text=appeal.text,
            original_reason=appeal.original_reason,
            status=appeal.status,
            review_message_id=appeal.review_message_id,
            resolver_id=appeal.resolver_id,
            created_at=appeal.created_at.replace(tzinfo=None),
            resolved_at=(appeal.resolved_at.replace(tzinfo=None) if appeal.resolved_at else None),
        )
        self._session.add(row)
        await self._session.flush()  # нужен id (кнопки/панель ссылаются на апелляцию)
        return _to_domain(row)

    async def get(self, appeal_id: int) -> Appeal | None:
        row = await self._session.get(AppealModel, appeal_id)
        return _to_domain(row) if row is not None else None

    async def get_pending(self, guild_id: int, user_id: int) -> Appeal | None:
        stmt = (
            select(AppealModel)
            .where(
                AppealModel.guild_id == guild_id,
                AppealModel.user_id == user_id,
                AppealModel.status == STATUS_PENDING,
            )
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    async def list_pending(self, guild_id: int) -> list[Appeal]:
        stmt = (
            select(AppealModel)
            .where(AppealModel.guild_id == guild_id, AppealModel.status == STATUS_PENDING)
            .order_by(AppealModel.created_at.asc(), AppealModel.id.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(row) for row in rows]

    async def save(self, appeal: Appeal) -> None:
        assert appeal.id is not None
        row = await self._session.get(AppealModel, appeal.id)
        if row is None:
            return
        row.status = appeal.status
        row.text = appeal.text
        row.review_message_id = appeal.review_message_id
        row.resolver_id = appeal.resolver_id
        row.resolved_at = appeal.resolved_at.replace(tzinfo=None) if appeal.resolved_at else None
