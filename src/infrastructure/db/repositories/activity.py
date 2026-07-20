from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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


def _upsert(session: AsyncSession):
    """Диалект-специфичный INSERT с поддержкой ON CONFLICT. Postgres и SQLite
    (3.24+) оба умеют, но конструктор разный; интерфейс on_conflict_* общий."""
    name = session.bind.dialect.name if session.bind is not None else "sqlite"
    return pg_insert if name == "postgresql" else sqlite_insert


class SqlAlchemyMemberActivityRepository(IMemberActivityRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_last_message(self, user_id: int, guild_id: int) -> datetime | None:
        row = await self._session.get(MemberActivityModel, (user_id, guild_id))
        if row is None:
            return None
        return row.last_message_at.replace(tzinfo=UTC)

    async def set_last_message(self, user_id: int, guild_id: int, at: datetime) -> None:
        naive = at.replace(tzinfo=None)
        row = await self._session.get(MemberActivityModel, (user_id, guild_id))
        if row is None:
            self._session.add(
                MemberActivityModel(user_id=user_id, guild_id=guild_id, last_message_at=naive)
            )
        else:
            row.last_message_at = naive


class SqlAlchemyAlbumRepository(IAlbumRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def try_mark(self, guild_id: int, message_id: int, at: datetime) -> bool:
        # INSERT ... ON CONFLICT DO NOTHING: если пара уже есть — БД молча
        # пропускает, rowcount=0. Атомарно, без «проверить-потом-вставить»,
        # поэтому гонка двух реакций не задваивает и не роняет IntegrityError.
        stmt = (
            _upsert(self._session)(AlbumPostModel)
            .values(guild_id=guild_id, message_id=message_id, posted_at=at.replace(tzinfo=None))
            .on_conflict_do_nothing(index_elements=["guild_id", "message_id"])
        )
        result = await self._session.execute(stmt)
        return result.rowcount == 1


class SqlAlchemyVoiceProgressRepository(IVoiceProgressRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def load_all(self) -> dict[tuple[int, int], float]:
        rows = (await self._session.execute(select(VoiceProgressModel))).scalars().all()
        return {(row.guild_id, row.user_id): row.minutes for row in rows}

    async def save_many(
        self, progress: dict[tuple[int, int], float], accrued_minutes: float = 0.0
    ) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        insert = _upsert(self._session)
        for (guild_id, user_id), minutes in progress.items():
            # total_minutes += accrued считает САМА БД (table.total + accrued), а
            # не Python после чтения — иначе два писателя теряли бы инкремент.
            # minutes перезаписывается новым значением (это текущий остаток).
            stmt = insert(VoiceProgressModel).values(
                guild_id=guild_id,
                user_id=user_id,
                minutes=minutes,
                total_minutes=accrued_minutes,
                updated_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["guild_id", "user_id"],
                set_={
                    "minutes": stmt.excluded.minutes,
                    "total_minutes": VoiceProgressModel.total_minutes + accrued_minutes,
                    "updated_at": now,
                },
            )
            await self._session.execute(stmt)

    async def total_minutes(self, guild_id: int, user_id: int) -> float:
        row = await self._session.get(VoiceProgressModel, (guild_id, user_id))
        return row.total_minutes if row is not None else 0.0

    async def guild_total_minutes(self, guild_id: int) -> float:
        stmt = select(func.coalesce(func.sum(VoiceProgressModel.total_minutes), 0.0)).where(
            VoiceProgressModel.guild_id == guild_id
        )
        return float((await self._session.execute(stmt)).scalar_one())

    async def top_by_minutes(self, guild_id: int, limit: int) -> list[tuple[int, float]]:
        stmt = (
            select(VoiceProgressModel.user_id, VoiceProgressModel.total_minutes)
            .where(
                VoiceProgressModel.guild_id == guild_id,
                VoiceProgressModel.total_minutes > 0,
            )
            .order_by(VoiceProgressModel.total_minutes.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(uid, float(minutes)) for uid, minutes in rows]


class SqlAlchemyReminderRepository(IReminderRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, reminder: Reminder) -> None:
        self._session.add(
            ReminderModel(
                user_id=reminder.user_id,
                guild_id=reminder.guild_id,
                text=reminder.text,
                due_at=reminder.due_at.replace(tzinfo=None),
            )
        )

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
                due_at=row.due_at.replace(tzinfo=UTC),
            )
            for row in rows
        ]
        if rows:
            await self._session.execute(
                delete(ReminderModel).where(ReminderModel.due_at <= naive_now)
            )
        return due
