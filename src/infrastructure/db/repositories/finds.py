from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.finds.entities import CollectionItem, FindAttempt, NightFind
from src.domain.finds.repository import (
    ICollectionRepository,
    IFindAttemptRepository,
    INightFindRepository,
)
from src.infrastructure.db.models.finds import (
    CollectionItemModel,
    FindAttemptModel,
    NightFindModel,
)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _naive(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=None) if value is not None else None


def _find_to_domain(row: NightFindModel) -> NightFind:
    return NightFind(
        id=row.id,
        guild_id=row.guild_id,
        location_id=row.location_id,
        item_id=row.item_id,
        created_at=_aware(row.created_at),
        expires_at=_aware(row.expires_at),
        channel_id=row.channel_id,
        message_id=row.message_id,
        claimed_by=row.claimed_by,
        claimed_at=_aware(row.claimed_at),
    )


class SqlAlchemyNightFindRepository(INightFindRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, find: NightFind) -> NightFind:
        row = NightFindModel(
            guild_id=find.guild_id,
            location_id=find.location_id,
            item_id=find.item_id,
            created_at=_naive(find.created_at),
            expires_at=_naive(find.expires_at),
            channel_id=find.channel_id,
            message_id=find.message_id,
            claimed_by=find.claimed_by,
            claimed_at=_naive(find.claimed_at),
        )
        self._session.add(row)
        await self._session.flush()  # получить id
        find.id = row.id
        return find

    async def save(self, find: NightFind) -> None:
        if find.id is None:
            await self.add(find)
            return
        row = await self._session.get(NightFindModel, find.id)
        if row is None:
            return
        row.channel_id = find.channel_id
        row.message_id = find.message_id
        row.claimed_by = find.claimed_by
        row.claimed_at = _naive(find.claimed_at)
        row.expires_at = _naive(find.expires_at)

    async def get(self, find_id: int) -> NightFind | None:
        row = await self._session.get(NightFindModel, find_id)
        return _find_to_domain(row) if row else None

    async def get_by_message(self, message_id: int) -> NightFind | None:
        stmt = select(NightFindModel).where(
            NightFindModel.message_id == message_id
        ).limit(1)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _find_to_domain(row) if row else None

    async def get_active(self, guild_id: int, now: datetime) -> NightFind | None:
        stmt = (
            select(NightFindModel)
            .where(
                NightFindModel.guild_id == guild_id,
                NightFindModel.claimed_by.is_(None),
                NightFindModel.expires_at > _naive(now),
            )
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _find_to_domain(row) if row else None

    async def list_unclaimed(self, now: datetime) -> list[NightFind]:
        stmt = select(NightFindModel).where(
            NightFindModel.claimed_by.is_(None),
            NightFindModel.expires_at > _naive(now),
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_find_to_domain(row) for row in rows]

    async def claim_if_free(self, find_id: int, user_id: int, now: datetime) -> bool:
        result = await self._session.execute(
            update(NightFindModel)
            .where(
                NightFindModel.id == find_id,
                NightFindModel.claimed_by.is_(None),
            )
            .values(claimed_by=user_id, claimed_at=_naive(now))
        )
        return result.rowcount > 0


class SqlAlchemyCollectionRepository(ICollectionRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, item: CollectionItem) -> None:
        self._session.add(CollectionItemModel(
            guild_id=item.guild_id,
            user_id=item.user_id,
            item_id=item.item_id,
            obtained_at=_naive(item.obtained_at),
            gifted_at=_naive(item.gifted_at),
        ))

    async def list_for_user(self, guild_id: int, user_id: int) -> list[CollectionItem]:
        stmt = (
            select(CollectionItemModel)
            .where(
                CollectionItemModel.guild_id == guild_id,
                CollectionItemModel.user_id == user_id,
            )
            .order_by(CollectionItemModel.obtained_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            CollectionItem(
                id=row.id,
                guild_id=row.guild_id,
                user_id=row.user_id,
                item_id=row.item_id,
                obtained_at=_aware(row.obtained_at),
                gifted_at=_aware(row.gifted_at),
            )
            for row in rows
        ]

    async def get_ungifted(
        self, guild_id: int, user_id: int, item_id: str
    ) -> CollectionItem | None:
        stmt = (
            select(CollectionItemModel)
            .where(
                CollectionItemModel.guild_id == guild_id,
                CollectionItemModel.user_id == user_id,
                CollectionItemModel.item_id == item_id,
                CollectionItemModel.gifted_at.is_(None),
            )
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return CollectionItem(
            id=row.id,
            guild_id=row.guild_id,
            user_id=row.user_id,
            item_id=row.item_id,
            obtained_at=_aware(row.obtained_at),
            gifted_at=_aware(row.gifted_at),
        )

    async def mark_gifted(self, collection_item_id: int, now: datetime) -> None:
        row = await self._session.get(CollectionItemModel, collection_item_id)
        if row is not None:
            row.gifted_at = _naive(now)


class SqlAlchemyFindAttemptRepository(IFindAttemptRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, attempt: FindAttempt) -> None:
        self._session.add(FindAttemptModel(
            guild_id=attempt.guild_id,
            user_id=attempt.user_id,
            kind=attempt.kind,
            success=attempt.success,
            attempted_at=_naive(attempt.attempted_at),
            find_id=attempt.find_id,
        ))

    async def last_attempt_at(
        self, guild_id: int, user_id: int, kind: str
    ) -> datetime | None:
        stmt = (
            select(FindAttemptModel.attempted_at)
            .where(
                FindAttemptModel.guild_id == guild_id,
                FindAttemptModel.user_id == user_id,
                FindAttemptModel.kind == kind,
            )
            .order_by(FindAttemptModel.attempted_at.desc())
            .limit(1)
        )
        value = (await self._session.execute(stmt)).scalar_one_or_none()
        return _aware(value)

    async def has_attempted(self, find_id: int, user_id: int) -> bool:
        stmt = (
            select(FindAttemptModel.id)
            .where(
                FindAttemptModel.find_id == find_id,
                FindAttemptModel.user_id == user_id,
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None
