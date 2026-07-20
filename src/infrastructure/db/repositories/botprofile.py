from datetime import UTC

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.botprofile.entities import BotProfile
from src.domain.botprofile.repository import IBotProfileRepository
from src.infrastructure.db.models.botprofile import BotProfileModel


def _upsert(session: AsyncSession):
    name = session.bind.dialect.name if session.bind is not None else "sqlite"
    return pg_insert if name == "postgresql" else sqlite_insert


class SqlAlchemyBotProfileRepository(IBotProfileRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, guild_id: int) -> BotProfile | None:
        row = await self._session.get(BotProfileModel, guild_id)
        if row is None:
            return None
        return BotProfile(
            guild_id=row.guild_id,
            nick=row.nick,
            avatar_url=row.avatar_url,
            banner_url=row.banner_url,
            avatar_data=row.avatar_data,
            updated_at=row.updated_at.replace(tzinfo=UTC),
        )

    async def save(self, profile: BotProfile) -> None:
        assert profile.updated_at is not None
        values = {
            "guild_id": profile.guild_id,
            "nick": profile.nick,
            "avatar_url": profile.avatar_url,
            "banner_url": profile.banner_url,
            "avatar_data": profile.avatar_data,
            "updated_at": profile.updated_at.replace(tzinfo=None),
        }
        stmt = _upsert(self._session)(BotProfileModel).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["guild_id"],
            set_={k: v for k, v in values.items() if k != "guild_id"},
        )
        await self._session.execute(stmt)
