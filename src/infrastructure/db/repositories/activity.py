from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.activity.entities import Reminder
from src.domain.activity.repository import (
    IAlbumRepository,
    IMemberActivityRepository,
    IReminderRepository,
    IVoiceProgressRepository,
)
from src.infrastructure.db.models.activity import (
    AlbumPostModel,
    MemberActivityModel,
    ReminderModel,
    VoiceProgressModel,
)


class SqlAlchemyMemberActivityRepository(IMemberActivityRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_last_message(self, user_id: int, guild_id: int) -> datetime | None:
        row = await self._session.get(MemberActivityModel, (user_id, guild_id))
        if row is None:
            return None
        return row.last_message_at.replace(tzinfo=timezone.utc)

    async def set_last_message(self, user_id: int, guild_id: int, at: datetime) -> None:
        naive = at.replace(tzinfo=None)
        row = await self._session.get(MemberActivityModel, (user_id, guild_id))
        if row is None:
            self._session.add(MemberActivityModel(
                user_id=user_id, guild_id=guild_id, last_message_at=naive
            ))
        else:
            row.last_message_at = naive


class SqlAlchemyAlbumRepository(IAlbumRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def was_posted(self, guild_id: int, message_id: int) -> bool:
        row = await self._session.get(AlbumPostModel, (guild_id, message_id))
        return row is not None

    async def mark_posted(self, guild_id: int, message_id: int, at: datetime) -> None:
        self._session.add(AlbumPostModel(
            guild_id=guild_id,
            message_id=message_id,
            posted_at=at.replace(tzinfo=None),
        ))


class SqlAlchemyVoiceProgressRepository(IVoiceProgressRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def load_all(self) -> dict[tuple[int, int], float]:
        rows = (await self._session.execute(select(VoiceProgressModel))).scalars().all()
        return {(row.guild_id, row.user_id): row.minutes for row in rows}

    async def save_many(
        self, progress: dict[tuple[int, int], float], accrued_minutes: float = 0.0
    ) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for (guild_id, user_id), minutes in progress.items():
            row = await self._session.get(VoiceProgressModel, (guild_id, user_id))
            if row is None:
                self._session.add(VoiceProgressModel(
                    guild_id=guild_id, user_id=user_id,
                    minutes=minutes, total_minutes=accrued_minutes, updated_at=now,
                ))
            else:
                row.minutes = minutes
                row.total_minutes += accrued_minutes
                row.updated_at = now

    async def total_minutes(self, guild_id: int, user_id: int) -> float:
        row = await self._session.get(VoiceProgressModel, (guild_id, user_id))
        return row.total_minutes if row is not None else 0.0


class SqlAlchemyReminderRepository(IReminderRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, reminder: Reminder) -> None:
        self._session.add(ReminderModel(
            user_id=reminder.user_id,
            guild_id=reminder.guild_id,
            text=reminder.text,
            due_at=reminder.due_at.replace(tzinfo=None),
        ))

    async def pop_due(self, now: datetime) -> list[Reminder]:
        naive_now = now.replace(tzinfo=None)
        stmt = select(ReminderModel).where(ReminderModel.due_at <= naive_now)
        rows = (await self._session.execute(stmt)).scalars().all()
        due = [
            Reminder(
                id=row.id,
                user_id=row.user_id,
                guild_id=row.guild_id,
                text=row.text,
                due_at=row.due_at.replace(tzinfo=timezone.utc),
            )
            for row in rows
        ]
        if rows:
            await self._session.execute(
                delete(ReminderModel).where(ReminderModel.due_at <= naive_now)
            )
        return due
