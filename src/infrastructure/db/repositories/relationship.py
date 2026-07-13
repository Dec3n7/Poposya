from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.relationship.entities import RelationshipProfile, SecretCode, SecretRoom
from src.domain.relationship.repository import (
    IDialogSummaryRepository,
    IRelationshipRepository,
    ISecretRoomRepository,
)
from src.infrastructure.db.models.relationship import (
    DialogSummaryModel,
    RelationshipProfileModel,
    SecretCodeModel,
    SecretRoomModel,
)


class SqlAlchemyDialogSummaryRepository(IDialogSummaryRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, guild_id: int, user_id: int, summary: str, at: datetime, keep: int) -> None:
        self._session.add(
            DialogSummaryModel(
                guild_id=guild_id,
                user_id=user_id,
                summary=summary,
                created_at=at.replace(tzinfo=None),
            )
        )
        await self._session.flush()
        # держим только последние keep записей
        stmt = (
            select(DialogSummaryModel.id)
            .where(
                DialogSummaryModel.guild_id == guild_id,
                DialogSummaryModel.user_id == user_id,
            )
            .order_by(DialogSummaryModel.created_at.desc())
            .offset(keep)
        )
        stale_ids = [row for row in (await self._session.execute(stmt)).scalars().all()]
        if stale_ids:
            await self._session.execute(
                delete(DialogSummaryModel).where(DialogSummaryModel.id.in_(stale_ids))
            )

    async def last(self, guild_id: int, user_id: int, limit: int) -> list[str]:
        stmt = (
            select(DialogSummaryModel.summary)
            .where(
                DialogSummaryModel.guild_id == guild_id,
                DialogSummaryModel.user_id == user_id,
            )
            .order_by(DialogSummaryModel.created_at.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(reversed(rows))  # старые -> новые


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _naive(value):
    return value.replace(tzinfo=None) if value is not None else None


def _to_domain(row: RelationshipProfileModel) -> RelationshipProfile:
    return RelationshipProfile(
        user_id=row.user_id,
        guild_id=row.guild_id,
        points=row.points,
        points_awarded_today=row.points_awarded_today,
        last_award_date=row.last_award_date,
        is_exclusive=row.is_exclusive,
        frozen_by_admin=row.frozen_by_admin,
        last_dialog_at=_aware(row.last_dialog_at),
        user_notes=row.user_notes,
        survey_gender=row.survey_gender,
        survey_contact=row.survey_contact,
        survey_interests=row.survey_interests,
        survey_season=row.survey_season,
        survey_completed_at=_aware(row.survey_completed_at),
        birthday_day=row.birthday_day,
        birthday_month=row.birthday_month,
        birthday_reminded_at=row.birthday_reminded_at,
        birthday_congratulated_at=row.birthday_congratulated_at,
        deep_dialogs=row.deep_dialogs,
        last_decay_at=row.last_decay_at,
    )


def _apply(row: RelationshipProfileModel, profile: RelationshipProfile) -> None:
    row.points = profile.points
    row.points_awarded_today = profile.points_awarded_today
    row.last_award_date = profile.last_award_date
    row.is_exclusive = profile.is_exclusive
    row.frozen_by_admin = profile.frozen_by_admin
    row.last_dialog_at = _naive(profile.last_dialog_at)
    row.user_notes = profile.user_notes
    row.survey_gender = profile.survey_gender
    row.survey_contact = profile.survey_contact
    row.survey_interests = profile.survey_interests
    row.survey_season = profile.survey_season
    row.survey_completed_at = _naive(profile.survey_completed_at)
    row.birthday_day = profile.birthday_day
    row.birthday_month = profile.birthday_month
    row.birthday_reminded_at = profile.birthday_reminded_at
    row.birthday_congratulated_at = profile.birthday_congratulated_at
    row.deep_dialogs = profile.deep_dialogs
    row.last_decay_at = profile.last_decay_at


class SqlAlchemySecretRoomRepository(ISecretRoomRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_code(self, user_id: int, guild_id: int) -> SecretCode | None:
        row = await self._session.get(SecretCodeModel, (guild_id, user_id))
        if row is None:
            return None
        return SecretCode(
            guild_id=row.guild_id,
            user_id=row.user_id,
            code=row.code,
            issued_at=_aware(row.issued_at),
            used_at=_aware(row.used_at),
        )

    async def save_code(self, code: SecretCode) -> None:
        row = await self._session.get(SecretCodeModel, (code.guild_id, code.user_id))
        if row is None:
            row = SecretCodeModel(guild_id=code.guild_id, user_id=code.user_id)
            self._session.add(row)
        row.code = code.code
        row.issued_at = _naive(code.issued_at)
        row.used_at = _naive(code.used_at)

    async def get_active_room(self, guild_id: int, now: datetime) -> SecretRoom | None:
        stmt = (
            select(SecretRoomModel)
            .where(
                SecretRoomModel.guild_id == guild_id,
                SecretRoomModel.expires_at > now.replace(tzinfo=None),
            )
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._room_to_domain(row) if row else None

    async def add_room(self, room: SecretRoom) -> None:
        self._session.add(
            SecretRoomModel(
                guild_id=room.guild_id,
                text_channel_id=room.text_channel_id,
                voice_channel_id=room.voice_channel_id,
                expires_at=_naive(room.expires_at),
                created_by=room.created_by,
            )
        )

    async def pop_expired_rooms(self, now: datetime) -> list[SecretRoom]:
        naive_now = now.replace(tzinfo=None)
        stmt = select(SecretRoomModel).where(SecretRoomModel.expires_at <= naive_now)
        rows = (await self._session.execute(stmt)).scalars().all()
        expired = [self._room_to_domain(row) for row in rows]
        if rows:
            await self._session.execute(
                delete(SecretRoomModel).where(SecretRoomModel.expires_at <= naive_now)
            )
        return expired

    @staticmethod
    def _room_to_domain(row: SecretRoomModel) -> SecretRoom:
        return SecretRoom(
            id=row.id,
            guild_id=row.guild_id,
            text_channel_id=row.text_channel_id,
            voice_channel_id=row.voice_channel_id,
            expires_at=_aware(row.expires_at),
            created_by=row.created_by,
        )


class SqlAlchemyRelationshipRepository(IRelationshipRepository):
    """Identity map на время транзакции: один и тот же профиль всегда
    возвращается одним доменным объектом, чтобы use case не работал
    с двумя копиями одной строки."""

    def __init__(self, session: AsyncSession):
        self._session = session
        self._identity: dict[tuple[int, int], RelationshipProfile] = {}

    async def get(self, user_id: int, guild_id: int) -> RelationshipProfile | None:
        key = (user_id, guild_id)
        if key in self._identity:
            return self._identity[key]
        row = await self._session.get(RelationshipProfileModel, key)
        if row is None:
            return None
        profile = _to_domain(row)
        self._identity[key] = profile
        return profile

    async def get_or_create(self, user_id: int, guild_id: int) -> RelationshipProfile:
        profile = await self.get(user_id, guild_id)
        if profile is None:
            profile = RelationshipProfile(user_id=user_id, guild_id=guild_id)
            self._identity[(user_id, guild_id)] = profile
        return profile

    async def get_exclusive_holder(self, guild_id: int) -> RelationshipProfile | None:
        stmt = (
            select(RelationshipProfileModel)
            .where(
                RelationshipProfileModel.guild_id == guild_id,
                RelationshipProfileModel.is_exclusive.is_(True),
            )
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        key = (row.user_id, row.guild_id)
        if key in self._identity:
            return self._identity[key]
        profile = _to_domain(row)
        self._identity[key] = profile
        return profile

    async def find_birthdays(self, month: int, day: int) -> list[RelationshipProfile]:
        stmt = select(RelationshipProfileModel).where(
            RelationshipProfileModel.birthday_month == month,
            RelationshipProfileModel.birthday_day == day,
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        result = []
        for row in rows:
            key = (row.user_id, row.guild_id)
            if key not in self._identity:
                self._identity[key] = _to_domain(row)
            result.append(self._identity[key])
        return result

    def _cached(self, rows) -> list[RelationshipProfile]:
        result = []
        for row in rows:
            key = (row.user_id, row.guild_id)
            if key not in self._identity:
                self._identity[key] = _to_domain(row)
            result.append(self._identity[key])
        return result

    async def top_by_points(self, guild_id: int, limit: int) -> list[RelationshipProfile]:
        stmt = (
            select(RelationshipProfileModel)
            .where(
                RelationshipProfileModel.guild_id == guild_id,
                RelationshipProfileModel.points > 0,
            )
            .order_by(RelationshipProfileModel.points.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return self._cached(rows)

    async def list_decayable(
        self, inactive_before: datetime, decayed_before: datetime
    ) -> list[RelationshipProfile]:
        from sqlalchemy import or_

        stmt = select(RelationshipProfileModel).where(
            RelationshipProfileModel.points > 0,
            RelationshipProfileModel.frozen_by_admin.is_(False),
            RelationshipProfileModel.last_dialog_at.is_not(None),
            RelationshipProfileModel.last_dialog_at < inactive_before.replace(tzinfo=None),
            or_(
                RelationshipProfileModel.last_decay_at.is_(None),
                RelationshipProfileModel.last_decay_at <= decayed_before.date(),
            ),
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return self._cached(rows)

    async def save(self, profile: RelationshipProfile) -> None:
        key = (profile.user_id, profile.guild_id)
        row = await self._session.get(RelationshipProfileModel, key)
        if row is None:
            row = RelationshipProfileModel(user_id=profile.user_id, guild_id=profile.guild_id)
            _apply(row, profile)
            self._session.add(row)
        else:
            _apply(row, profile)
