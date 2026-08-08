from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.achievements.entities import UnlockedAchievement
from src.domain.achievements.repository import IAchievementRepository
from src.infrastructure.db.models.achievements import UnlockedAchievementModel


def _upsert(session: AsyncSession):
    name = session.bind.dialect.name if session.bind is not None else "sqlite"
    return pg_insert if name == "postgresql" else sqlite_insert


class SqlAlchemyAchievementRepository(IAchievementRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def unlocked_ids(self, user_id: int, guild_id: int) -> set[str]:
        rows = await self._session.execute(
            select(UnlockedAchievementModel.achievement_id).where(
                UnlockedAchievementModel.user_id == user_id,
                UnlockedAchievementModel.guild_id == guild_id,
            )
        )
        return set(rows.scalars())

    async def add(self, unlocked: UnlockedAchievement) -> None:
        # ON CONFLICT DO NOTHING по составному PK — повторная выдача той же
        # ачивки молча отсекается, без IntegrityError и дублей
        stmt = (
            _upsert(self._session)(UnlockedAchievementModel)
            .values(
                user_id=unlocked.user_id,
                guild_id=unlocked.guild_id,
                achievement_id=unlocked.achievement_id,
                unlocked_at=unlocked.unlocked_at.replace(tzinfo=None),
            )
            .on_conflict_do_nothing(index_elements=["user_id", "guild_id", "achievement_id"])
        )
        await self._session.execute(stmt)

    async def list_for_user(self, user_id: int, guild_id: int) -> list[UnlockedAchievement]:
        rows = await self._session.execute(
            select(UnlockedAchievementModel)
            .where(
                UnlockedAchievementModel.user_id == user_id,
                UnlockedAchievementModel.guild_id == guild_id,
            )
            .order_by(UnlockedAchievementModel.unlocked_at.desc())
        )
        return [
            UnlockedAchievement(
                user_id=m.user_id,
                guild_id=m.guild_id,
                achievement_id=m.achievement_id,
                unlocked_at=m.unlocked_at,
            )
            for m in rows.scalars()
        ]
