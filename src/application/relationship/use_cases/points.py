"""Ядро очков: начисление за сообщение/войс, ранг, лидерборд, заморозка,
админская коррекция. Пересчёт роли и титула «Единственного» - здесь."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from src.domain.relationship.entities import RelationshipProfile
from src.domain.relationship.events import ExclusiveTransferred, RelationshipRoleChanged
from src.domain.relationship.policies import PointsToLevelPolicy
from src.domain.shared.holidays import HolidayCalendar

from ._common import (
    SurveyData,
    UowFactory,
    _policy_of,
    _reevaluate_exclusive,
    _survey_of,
)


@dataclass(frozen=True)
class AwardResult:
    points: int
    level: int  # тон промпта 1-7
    role_index: int | None
    previous_role_index: int | None
    point_awarded: bool
    is_exclusive: bool
    became_exclusive: bool
    returning_after_absence: bool
    user_notes: str
    survey: SurveyData = SurveyData()
    recent_summaries: tuple[str, ...] = ()  # память о прошлых разговорах


@dataclass(frozen=True)
class RankInfo:
    points: int
    level: int
    role_index: int | None
    is_exclusive: bool
    frozen: bool
    next_threshold: int | None
    user_notes: str = ""
    survey: SurveyData = SurveyData()
    birthday_day: int | None = None
    birthday_month: int | None = None
    deep_dialogs: int = 0
    last_dialog_at: datetime | None = None


class AwardPointUseCase:
    def __init__(
        self,
        uow_factory: UowFactory,
        policy: PointsToLevelPolicy,
        daily_cap: int,
        absence_days: int,
        calendar: HolidayCalendar | None = None,
        holiday_multiplier: int = 2,
        settings_provider=None,
    ):
        self._uow_factory = uow_factory
        self._policy = policy
        self._daily_cap = daily_cap
        self._absence_days = absence_days
        self._calendar = calendar
        self._holiday_multiplier = holiday_multiplier
        self._settings = settings_provider

    async def execute(
        self,
        user_id: int,
        guild_id: int,
        channel_id: int,
        now: datetime,
        base_amount: int = 1,
    ) -> AwardResult:
        """base_amount - очков за одно событие (сообщение = 1, час в войсе = 3);
        в праздники умножается, дневной потолок общий для всех источников."""
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get_or_create(user_id, guild_id)

            # пер-серверные настройки: политика ролей, потолок, отсутствие, праздник
            gs = self._settings.resolved(guild_id) if self._settings is not None else None
            policy = gs.points_policy() if gs is not None else self._policy
            absence_days = gs.relationship_absence_days if gs is not None else self._absence_days
            holiday_multiplier = (
                gs.holiday_points_multiplier if gs is not None else self._holiday_multiplier
            )
            daily_cap = gs.relationship_daily_point_cap if gs is not None else self._daily_cap

            returning = (
                profile.last_dialog_at is not None
                and now - profile.last_dialog_at > timedelta(days=absence_days)
            )
            old_role = policy.role_index(profile.points, profile.is_exclusive)

            # в праздники очки идут с множителем (и потолок пропорционально выше)
            is_holiday = (
                self._calendar is not None and self._calendar.holiday_name(now.date()) is not None
            )
            multiplier = holiday_multiplier if is_holiday else 1
            awarded = profile.award_point(
                now, daily_cap * multiplier, amount=base_amount * multiplier
            )

            previous_holder: RelationshipProfile | None = None
            became_exclusive = False
            if awarded:
                holder = await uow.relationships.get_exclusive_holder(guild_id)
                previous_holder = _reevaluate_exclusive(profile, holder, policy.exclusive_threshold)
                became_exclusive = (
                    profile.is_exclusive
                    and (holder is None or holder.user_id != profile.user_id)
                    and old_role != policy.exclusive_role_index
                )

            new_role = policy.role_index(profile.points, profile.is_exclusive)

            await uow.relationships.save(profile)
            if previous_holder is not None:
                await uow.relationships.save(previous_holder)

            if new_role != old_role:
                uow.add_event(
                    RelationshipRoleChanged(
                        aggregate_id=f"{guild_id}:{user_id}",
                        guild_id=guild_id,
                        user_id=user_id,
                        channel_id=channel_id,
                        old_role_index=old_role,
                        new_role_index=new_role,
                        points=profile.points,
                    )
                )
            if became_exclusive:
                uow.add_event(
                    ExclusiveTransferred(
                        aggregate_id=str(guild_id),
                        guild_id=guild_id,
                        new_user_id=user_id,
                        previous_user_id=previous_holder.user_id if previous_holder else None,
                        channel_id=channel_id,
                    )
                )

            summaries = await uow.dialog_summaries.last(guild_id, user_id, 3)

            await uow.commit()

            return AwardResult(
                points=profile.points,
                level=policy.level(profile.points, profile.is_exclusive),
                role_index=new_role,
                previous_role_index=old_role,
                point_awarded=awarded,
                is_exclusive=profile.is_exclusive,
                became_exclusive=became_exclusive,
                returning_after_absence=returning,
                user_notes=profile.user_notes,
                survey=_survey_of(profile),
                recent_summaries=tuple(summaries),
            )


class GetRankUseCase:
    def __init__(
        self, uow_factory: UowFactory, policy: PointsToLevelPolicy, settings_provider=None
    ):
        self._uow_factory = uow_factory
        self._policy = policy
        self._settings = settings_provider

    async def execute(self, user_id: int, guild_id: int) -> RankInfo:
        policy = _policy_of(self._settings, guild_id, self._policy)
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get(user_id, guild_id)
            if profile is None:
                profile = RelationshipProfile(user_id=user_id, guild_id=guild_id)
            return RankInfo(
                points=profile.points,
                level=policy.level(profile.points, profile.is_exclusive),
                role_index=policy.role_index(profile.points, profile.is_exclusive),
                is_exclusive=profile.is_exclusive,
                frozen=profile.frozen_by_admin,
                next_threshold=policy.next_threshold(profile.points),
                user_notes=profile.user_notes,
                survey=_survey_of(profile),
                birthday_day=profile.birthday_day,
                birthday_month=profile.birthday_month,
                deep_dialogs=profile.deep_dialogs,
                last_dialog_at=profile.last_dialog_at,
            )


class SetPointsUseCase:
    """Админская коррекция очков; после неё - тот же пересчёт лидерства."""

    def __init__(
        self, uow_factory: UowFactory, policy: PointsToLevelPolicy, settings_provider=None
    ):
        self._uow_factory = uow_factory
        self._policy = policy
        self._settings = settings_provider

    async def execute(self, user_id: int, guild_id: int, points: int) -> RankInfo:
        policy = _policy_of(self._settings, guild_id, self._policy)
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get_or_create(user_id, guild_id)
            old_role = policy.role_index(profile.points, profile.is_exclusive)
            profile.points = max(0, points)

            holder = await uow.relationships.get_exclusive_holder(guild_id)
            previous_holder = _reevaluate_exclusive(profile, holder, policy.exclusive_threshold)
            new_role = policy.role_index(profile.points, profile.is_exclusive)

            await uow.relationships.save(profile)
            if previous_holder is not None:
                await uow.relationships.save(previous_holder)
            if new_role != old_role:
                uow.add_event(
                    RelationshipRoleChanged(
                        aggregate_id=f"{guild_id}:{user_id}",
                        guild_id=guild_id,
                        user_id=user_id,
                        old_role_index=old_role,
                        new_role_index=new_role,
                        points=profile.points,
                    )
                )
            await uow.commit()
            return RankInfo(
                points=profile.points,
                level=policy.level(profile.points, profile.is_exclusive),
                role_index=new_role,
                is_exclusive=profile.is_exclusive,
                frozen=profile.frozen_by_admin,
                next_threshold=policy.next_threshold(profile.points),
            )


class ToggleFreezeUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int) -> bool:
        """Возвращает новое состояние заморозки."""
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get_or_create(user_id, guild_id)
            profile.frozen_by_admin = not profile.frozen_by_admin
            await uow.relationships.save(profile)
            await uow.commit()
            return profile.frozen_by_admin


@dataclass(frozen=True)
class LeaderboardEntry:
    user_id: int
    points: int
    role_index: int | None
    is_exclusive: bool


class GetLeaderboardUseCase:
    def __init__(
        self, uow_factory: UowFactory, policy: PointsToLevelPolicy, settings_provider=None
    ):
        self._uow_factory = uow_factory
        self._policy = policy
        self._settings = settings_provider

    async def execute(self, guild_id: int, limit: int = 10) -> list[LeaderboardEntry]:
        policy = _policy_of(self._settings, guild_id, self._policy)
        async with self._uow_factory() as uow:
            profiles = await uow.relationships.top_by_points(guild_id, limit)
            return [
                LeaderboardEntry(
                    user_id=p.user_id,
                    points=p.points,
                    role_index=policy.role_index(p.points, p.is_exclusive),
                    is_exclusive=p.is_exclusive,
                )
                for p in profiles
            ]
