import secrets as _secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.relationship.entities import RelationshipProfile, SecretCode, SecretRoom
from src.domain.relationship.events import ExclusiveTransferred, RelationshipRoleChanged
from src.domain.relationship.policies import PointsToLevelPolicy
from src.domain.shared.holidays import HolidayCalendar

UowFactory = Callable[[], IUnitOfWork]


@dataclass(frozen=True)
class SurveyData:
    gender: str = ""
    contact: str = ""  # "quiet" | "normal" | "attention" ("" = normal)
    interests: str = ""
    season: str = ""
    completed: bool = False


def _survey_of(profile: RelationshipProfile) -> SurveyData:
    return SurveyData(
        gender=profile.survey_gender,
        contact=profile.survey_contact,
        interests=profile.survey_interests,
        season=profile.survey_season,
        completed=profile.survey_completed_at is not None,
    )


@dataclass(frozen=True)
class AwardResult:
    points: int
    level: int  # тон промпта 1–7
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


def _reevaluate_exclusive(
    profile: RelationshipProfile,
    holder: RelationshipProfile | None,
    exclusive_threshold: int,
) -> RelationshipProfile | None:
    """Правило «Единственного»: титул у лидера с очками >= порога; переходит
    только при СТРОГОМ превышении очков держателя (защита от мигания).
    Возвращает бывшего держателя, если титул перешёл."""
    if profile.points < exclusive_threshold or profile.is_exclusive:
        return None
    if holder is None:
        profile.is_exclusive = True
        return None
    if profile.points > holder.points:
        holder.is_exclusive = False
        profile.is_exclusive = True
        return holder
    return None


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
        """base_amount — очков за одно событие (сообщение = 1, час в войсе = 3);
        в праздники умножается, дневной потолок общий для всех источников."""
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get_or_create(user_id, guild_id)

            returning = (
                profile.last_dialog_at is not None
                and now - profile.last_dialog_at > timedelta(days=self._absence_days)
            )
            old_role = self._policy.role_index(profile.points, profile.is_exclusive)

            # в праздники очки идут с множителем (и потолок пропорционально выше)
            is_holiday = (
                self._calendar is not None and self._calendar.holiday_name(now.date()) is not None
            )
            multiplier = self._holiday_multiplier if is_holiday else 1
            daily_cap = (
                self._settings.get(guild_id, "relationship_daily_point_cap", self._daily_cap)
                if self._settings is not None
                else self._daily_cap
            )
            awarded = profile.award_point(
                now, daily_cap * multiplier, amount=base_amount * multiplier
            )

            previous_holder: RelationshipProfile | None = None
            became_exclusive = False
            if awarded:
                holder = await uow.relationships.get_exclusive_holder(guild_id)
                previous_holder = _reevaluate_exclusive(
                    profile, holder, self._policy.exclusive_threshold
                )
                became_exclusive = (
                    profile.is_exclusive
                    and (holder is None or holder.user_id != profile.user_id)
                    and old_role != self._policy.exclusive_role_index
                )

            new_role = self._policy.role_index(profile.points, profile.is_exclusive)

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
                level=self._policy.level(profile.points, profile.is_exclusive),
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
    def __init__(self, uow_factory: UowFactory, policy: PointsToLevelPolicy):
        self._uow_factory = uow_factory
        self._policy = policy

    async def execute(self, user_id: int, guild_id: int) -> RankInfo:
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get(user_id, guild_id)
            if profile is None:
                profile = RelationshipProfile(user_id=user_id, guild_id=guild_id)
            return RankInfo(
                points=profile.points,
                level=self._policy.level(profile.points, profile.is_exclusive),
                role_index=self._policy.role_index(profile.points, profile.is_exclusive),
                is_exclusive=profile.is_exclusive,
                frozen=profile.frozen_by_admin,
                next_threshold=self._policy.next_threshold(profile.points),
                user_notes=profile.user_notes,
                survey=_survey_of(profile),
                birthday_day=profile.birthday_day,
                birthday_month=profile.birthday_month,
                deep_dialogs=profile.deep_dialogs,
                last_dialog_at=profile.last_dialog_at,
            )


class SetPointsUseCase:
    """Админская коррекция очков; после неё — тот же пересчёт лидерства."""

    def __init__(self, uow_factory: UowFactory, policy: PointsToLevelPolicy):
        self._uow_factory = uow_factory
        self._policy = policy

    async def execute(self, user_id: int, guild_id: int, points: int) -> RankInfo:
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get_or_create(user_id, guild_id)
            old_role = self._policy.role_index(profile.points, profile.is_exclusive)
            profile.points = max(0, points)

            holder = await uow.relationships.get_exclusive_holder(guild_id)
            previous_holder = _reevaluate_exclusive(
                profile, holder, self._policy.exclusive_threshold
            )
            new_role = self._policy.role_index(profile.points, profile.is_exclusive)

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
                level=self._policy.level(profile.points, profile.is_exclusive),
                role_index=new_role,
                is_exclusive=profile.is_exclusive,
                frozen=profile.frozen_by_admin,
                next_threshold=self._policy.next_threshold(profile.points),
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


class UpdateUserNotesUseCase:
    def __init__(self, uow_factory: UowFactory, max_chars: int):
        self._uow_factory = uow_factory
        self._max_chars = max_chars

    async def execute(self, user_id: int, guild_id: int, notes: str) -> None:
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get_or_create(user_id, guild_id)
            profile.user_notes = notes.strip()[: self._max_chars]
            await uow.relationships.save(profile)
            await uow.commit()


class SetBirthdayUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int, day: int, month: int) -> bool:
        """False — такой даты не существует."""
        try:
            date(2000, month, day)  # 2000 — високосный: 29 февраля допустимо
        except ValueError:
            return False
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get_or_create(user_id, guild_id)
            profile.birthday_day = day
            profile.birthday_month = month
            await uow.relationships.save(profile)
            await uow.commit()
            return True


@dataclass(frozen=True)
class BirthdayEvents:
    remind: list[tuple[int, int]]  # (guild_id, user_id) — ДР через N дней
    congratulate: list[tuple[int, int]]  # (guild_id, user_id) — ДР сегодня


class BirthdayTickUseCase:
    """Периодическая проверка: кого напомнить (за remind_days) и кого
    поздравить сегодня. Дедупликация по году хранится в БД."""

    def __init__(self, uow_factory: UowFactory, remind_days: int):
        self._uow_factory = uow_factory
        self._remind_days = remind_days

    async def execute(self, now: datetime) -> BirthdayEvents:
        today = now.date()
        target = today + timedelta(days=self._remind_days)
        async with self._uow_factory() as uow:
            remind: list[tuple[int, int]] = []
            for profile in await uow.relationships.find_birthdays(target.month, target.day):
                marker = profile.birthday_reminded_at
                if marker is None or marker.year < today.year:
                    profile.birthday_reminded_at = today
                    await uow.relationships.save(profile)
                    remind.append((profile.guild_id, profile.user_id))

            congratulate: list[tuple[int, int]] = []
            for profile in await uow.relationships.find_birthdays(today.month, today.day):
                marker = profile.birthday_congratulated_at
                if marker is None or marker.year < today.year:
                    profile.birthday_congratulated_at = today
                    await uow.relationships.save(profile)
                    congratulate.append((profile.guild_id, profile.user_id))

            await uow.commit()
            return BirthdayEvents(remind=remind, congratulate=congratulate)


@dataclass(frozen=True)
class LeaderboardEntry:
    user_id: int
    points: int
    role_index: int | None
    is_exclusive: bool


class GetLeaderboardUseCase:
    def __init__(self, uow_factory: UowFactory, policy: PointsToLevelPolicy):
        self._uow_factory = uow_factory
        self._policy = policy

    async def execute(self, guild_id: int, limit: int = 10) -> list[LeaderboardEntry]:
        async with self._uow_factory() as uow:
            profiles = await uow.relationships.top_by_points(guild_id, limit)
            return [
                LeaderboardEntry(
                    user_id=p.user_id,
                    points=p.points,
                    role_index=self._policy.role_index(p.points, p.is_exclusive),
                    is_exclusive=p.is_exclusive,
                )
                for p in profiles
            ]


@dataclass(frozen=True)
class DecayResult:
    decayed: int
    transfers: list[tuple[int, int, int]]  # (guild_id, new_holder, old_holder)


class DecayPointsUseCase:
    """Мягкое угасание: после after_days тишины профиль теряет amount очков
    каждые every_days. Роль пересчитывается тихо; титул «Единственного»
    переходит, если лидера обогнали из-за угасания."""

    def __init__(
        self,
        uow_factory: UowFactory,
        policy: PointsToLevelPolicy,
        after_days: int,
        every_days: int,
        amount: int,
    ):
        self._uow_factory = uow_factory
        self._policy = policy
        self._after_days = after_days
        self._every_days = every_days
        self._amount = amount

    async def execute(self, now: datetime) -> DecayResult:
        inactive_before = now - timedelta(days=self._after_days)
        decayed_before = now - timedelta(days=self._every_days)
        transfers: list[tuple[int, int, int]] = []
        async with self._uow_factory() as uow:
            profiles = await uow.relationships.list_decayable(inactive_before, decayed_before)
            touched_guilds: set[int] = set()
            for profile in profiles:
                old_role = self._policy.role_index(profile.points, profile.is_exclusive)
                profile.points = max(0, profile.points - self._amount)
                profile.last_decay_at = now.date()
                new_role = self._policy.role_index(profile.points, profile.is_exclusive)
                await uow.relationships.save(profile)
                touched_guilds.add(profile.guild_id)
                if new_role != old_role and not profile.is_exclusive:
                    uow.add_event(
                        RelationshipRoleChanged(
                            aggregate_id=f"{profile.guild_id}:{profile.user_id}",
                            guild_id=profile.guild_id,
                            user_id=profile.user_id,
                            old_role_index=old_role,
                            new_role_index=new_role,
                            points=profile.points,
                        )
                    )

            # угасание могло сместить лидера — проверяем титул в затронутых гильдиях
            for guild_id in touched_guilds:
                holder = await uow.relationships.get_exclusive_holder(guild_id)
                if holder is None:
                    continue
                top = await uow.relationships.top_by_points(guild_id, 1)
                if not top:
                    continue
                challenger = top[0]
                if (
                    challenger.user_id != holder.user_id
                    and challenger.points > holder.points
                    and challenger.points >= self._policy.exclusive_threshold
                ):
                    holder.is_exclusive = False
                    challenger.is_exclusive = True
                    await uow.relationships.save(holder)
                    await uow.relationships.save(challenger)
                    for prof in (holder, challenger):
                        uow.add_event(
                            RelationshipRoleChanged(
                                aggregate_id=f"{guild_id}:{prof.user_id}",
                                guild_id=guild_id,
                                user_id=prof.user_id,
                                old_role_index=None,
                                new_role_index=self._policy.role_index(
                                    prof.points, prof.is_exclusive
                                ),
                                points=prof.points,
                            )
                        )
                    uow.add_event(
                        ExclusiveTransferred(
                            aggregate_id=str(guild_id),
                            guild_id=guild_id,
                            new_user_id=challenger.user_id,
                            previous_user_id=holder.user_id,
                        )
                    )
                    transfers.append((guild_id, challenger.user_id, holder.user_id))

            await uow.commit()
            return DecayResult(decayed=len(profiles), transfers=transfers)


class RecordDeepDialogUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int) -> None:
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get_or_create(user_id, guild_id)
            profile.deep_dialogs += 1
            await uow.relationships.save(profile)
            await uow.commit()


class AddDialogSummaryUseCase:
    def __init__(self, uow_factory: UowFactory, keep: int):
        self._uow_factory = uow_factory
        self._keep = keep

    async def execute(self, user_id: int, guild_id: int, summary: str, now: datetime) -> None:
        async with self._uow_factory() as uow:
            await uow.dialog_summaries.add(
                guild_id, user_id, summary.strip()[:400], now, self._keep
            )
            await uow.commit()


class IssueSecretCodeUseCase:
    """Выдаёт ключ секретной комнаты (или возвращает уже выданный
    неиспользованный)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int, now: datetime) -> str:
        async with self._uow_factory() as uow:
            existing = await uow.secret_rooms.get_code(user_id, guild_id)
            if existing is not None and existing.used_at is None:
                return existing.code
            raw = _secrets.token_hex(4).upper()
            code = f"{raw[:4]}-{raw[4:]}"
            await uow.secret_rooms.save_code(
                SecretCode(guild_id=guild_id, user_id=user_id, code=code, issued_at=now)
            )
            await uow.commit()
            return code


@dataclass(frozen=True)
class RedeemCheck:
    ok: bool
    reason: str = ""  # no_code | used | wrong | room_active
    active_room_channel_id: int | None = None


class ValidateSecretCodeUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self, user_id: int, guild_id: int, code_input: str, now: datetime
    ) -> RedeemCheck:
        async with self._uow_factory() as uow:
            room = await uow.secret_rooms.get_active_room(guild_id, now)
            if room is not None:
                return RedeemCheck(False, "room_active", room.text_channel_id)
            stored = await uow.secret_rooms.get_code(user_id, guild_id)
            if stored is None:
                return RedeemCheck(False, "no_code")
            if stored.used_at is not None:
                return RedeemCheck(False, "used")
            if stored.code.upper() != code_input.strip().upper():
                return RedeemCheck(False, "wrong")
            return RedeemCheck(True)


class RegisterSecretRoomUseCase:
    """Помечает ключ использованным и записывает комнату (после того, как
    ког создал каналы в Discord)."""

    def __init__(self, uow_factory: UowFactory, hours: int):
        self._uow_factory = uow_factory
        self._hours = hours

    async def execute(
        self,
        user_id: int,
        guild_id: int,
        text_channel_id: int,
        voice_channel_id: int,
        now: datetime,
    ) -> datetime:
        expires_at = now + timedelta(hours=self._hours)
        async with self._uow_factory() as uow:
            stored = await uow.secret_rooms.get_code(user_id, guild_id)
            if stored is not None:
                await uow.secret_rooms.save_code(replace(stored, used_at=now))
            await uow.secret_rooms.add_room(
                SecretRoom(
                    guild_id=guild_id,
                    text_channel_id=text_channel_id,
                    voice_channel_id=voice_channel_id,
                    expires_at=expires_at,
                    created_by=user_id,
                )
            )
            await uow.commit()
            return expires_at


class GetSecretCodeUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int) -> SecretCode | None:
        async with self._uow_factory() as uow:
            return await uow.secret_rooms.get_code(user_id, guild_id)


class PopExpiredSecretRoomsUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, now: datetime) -> list[SecretRoom]:
        async with self._uow_factory() as uow:
            expired = await uow.secret_rooms.pop_expired_rooms(now)
            await uow.commit()
            return expired


_SURVEY_CHOICE_FIELDS = {
    "gender": "survey_gender",
    "contact": "survey_contact",
    "season": "survey_season",
}


class SetSurveyChoiceUseCase:
    """Одиночный выбор анкеты (пол / внимание / время года)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int, field: str, value: str) -> None:
        attr = _SURVEY_CHOICE_FIELDS[field]
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get_or_create(user_id, guild_id)
            setattr(profile, attr, value[:50])
            await uow.relationships.save(profile)
            await uow.commit()


class ToggleSurveyInterestUseCase:
    """Интерес-тумблер; возвращает (добавлен ли, актуальный список)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int, interest: str) -> tuple[bool, list[str]]:
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get_or_create(user_id, guild_id)
            interests = [i for i in profile.survey_interests.split(",") if i]
            if interest in interests:
                interests.remove(interest)
                added = False
            else:
                interests.append(interest)
                added = True
            profile.survey_interests = ",".join(interests)[:500]
            await uow.relationships.save(profile)
            await uow.commit()
            return added, interests


@dataclass(frozen=True)
class SurveyCompleteResult:
    first_time: bool
    bonus_awarded: int
    survey: SurveyData


class CompleteSurveyUseCase:
    """Кнопка «Готово»: разовый бонус очков (вне дневного потолка) +
    тот же пересчёт роли и лидерства, что и у обычного начисления."""

    def __init__(self, uow_factory: UowFactory, policy: PointsToLevelPolicy, bonus: int):
        self._uow_factory = uow_factory
        self._policy = policy
        self._bonus = bonus

    async def execute(self, user_id: int, guild_id: int, now: datetime) -> SurveyCompleteResult:
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get_or_create(user_id, guild_id)
            first_time = profile.survey_completed_at is None
            bonus = 0
            if first_time:
                profile.survey_completed_at = now
                if not profile.frozen_by_admin:
                    bonus = self._bonus
                    old_role = self._policy.role_index(profile.points, profile.is_exclusive)
                    profile.points += bonus
                    holder = await uow.relationships.get_exclusive_holder(guild_id)
                    previous_holder = _reevaluate_exclusive(
                        profile, holder, self._policy.exclusive_threshold
                    )
                    if previous_holder is not None:
                        await uow.relationships.save(previous_holder)
                    new_role = self._policy.role_index(profile.points, profile.is_exclusive)
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
            await uow.relationships.save(profile)
            await uow.commit()
            return SurveyCompleteResult(
                first_time=first_time,
                bonus_awarded=bonus,
                survey=_survey_of(profile),
            )
