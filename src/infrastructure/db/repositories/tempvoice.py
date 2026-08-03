from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.tempvoice.entities import TempChannel
from src.domain.tempvoice.repository import ITempVoiceRepository
from src.infrastructure.db.dml import rows_affected
from src.infrastructure.db.models.tempvoice import TempVoiceChannelModel


def _to_domain(row: TempVoiceChannelModel) -> TempChannel:
    return TempChannel(
        guild_id=row.guild_id,
        channel_id=row.channel_id,
        owner_id=row.owner_id,
        created_at=row.created_at,
    )


class SqlAlchemyTempVoiceRepository(ITempVoiceRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def register(self, channel: TempChannel) -> None:
        self._session.add(
            TempVoiceChannelModel(
                channel_id=channel.channel_id,
                guild_id=channel.guild_id,
                owner_id=channel.owner_id,
                created_at=channel.created_at.replace(tzinfo=None),
            )
        )

    async def release(self, channel_id: int) -> bool:
        result = await self._session.execute(
            delete(TempVoiceChannelModel).where(TempVoiceChannelModel.channel_id == channel_id)
        )
        return rows_affected(result) > 0

    async def get(self, channel_id: int) -> TempChannel | None:
        row = await self._session.get(TempVoiceChannelModel, channel_id)
        return _to_domain(row) if row is not None else None

    async def set_owner(self, channel_id: int, owner_id: int) -> None:
        await self._session.execute(
            update(TempVoiceChannelModel)
            .where(TempVoiceChannelModel.channel_id == channel_id)
            .values(owner_id=owner_id)
        )

    async def list_for_guild(self, guild_id: int) -> list[TempChannel]:
        stmt = select(TempVoiceChannelModel).where(TempVoiceChannelModel.guild_id == guild_id)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(row) for row in rows]

    async def count_for_guild(self, guild_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(TempVoiceChannelModel)
            .where(TempVoiceChannelModel.guild_id == guild_id)
        )
        return int((await self._session.execute(stmt)).scalar_one())
