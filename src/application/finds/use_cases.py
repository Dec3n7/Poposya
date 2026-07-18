import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.application.relationship.use_cases import _reevaluate_exclusive
from src.domain.finds.catalog import (
    GIFT_BONUSES,
    Item,
    Location,
    get_item,
    get_location,
    roll_item,
    roll_location,
    roll_reward,
    success_chance,
)
from src.domain.finds.entities import CollectionItem, FindAttempt, NightFind, Rarity
from src.domain.finds.events import FindClaimed, FindFailed, ItemGifted
from src.domain.relationship.events import RelationshipRoleChanged
from src.domain.relationship.policies import PointsToLevelPolicy

UowFactory = Callable[[], IUnitOfWork]


def _append_note(notes: str, line: str, max_chars: int) -> str:
    """Дописать строку в заметки персоны; при переполнении режем старое."""
    combined = f"{notes}\n{line}".strip()
    return combined[-max_chars:] if len(combined) > max_chars else combined


async def _adjust_points(
    uow: IUnitOfWork,
    policy: PointsToLevelPolicy,
    guild_id: int,
    user_id: int,
    delta: int,
    channel_id: int = 0,
) -> int:
    """Начисление/списание очков отношений вне дневного потолка — с тем же
    пересчётом роли и «Единственного», что и у обычных начислений.
    Замороженные админом профили не трогаем. Возвращает итоговые очки."""
    profile = await uow.relationships.get_or_create(user_id, guild_id)
    if profile.frozen_by_admin or delta == 0:
        return profile.points
    old_role = policy.role_index(profile.points, profile.is_exclusive)
    profile.points = max(0, profile.points + delta)
    previous_holder = None
    if delta > 0:
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
                channel_id=channel_id,
                old_role_index=old_role,
                new_role_index=new_role,
                points=profile.points,
            )
        )
    return profile.points


class SpawnFindUseCase:
    """Создаёт находку, если на сервере нет активной. Локация и предмет
    выбираются здесь; анонс и message_id — забота кога."""

    def __init__(
        self,
        uow_factory: UowFactory,
        lifetime_hours: int,
        rng: random.Random | None = None,
    ):
        self._uow_factory = uow_factory
        self._lifetime = timedelta(hours=lifetime_hours)
        self._rng = rng or random.Random()

    async def execute(
        self,
        guild_id: int,
        now: datetime,
        season: str | None = None,
        boosted: bool = False,
        holiday: str | None = None,
    ) -> tuple[NightFind, Location, Item] | None:
        async with self._uow_factory() as uow:
            if await uow.night_finds.get_active(guild_id, now) is not None:
                return None
            location = roll_location(self._rng)
            item = roll_item(self._rng, season=season, boosted=boosted, holiday=holiday)
            find = await uow.night_finds.add(
                NightFind(
                    guild_id=guild_id,
                    location_id=location.id,
                    item_id=item.id,
                    created_at=now,
                    expires_at=now + self._lifetime,
                )
            )
            await uow.commit()
            return find, location, item


class RegisterFindMessageUseCase:
    """Привязывает анонс в Discord к находке (после отправки сообщения)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, find_id: int, channel_id: int, message_id: int) -> None:
        async with self._uow_factory() as uow:
            find = await uow.night_finds.get(find_id)
            if find is None:
                return
            find.channel_id = channel_id
            find.message_id = message_id
            await uow.night_finds.save(find)
            await uow.commit()


@dataclass(frozen=True)
class ClaimResult:
    # gone | already | cooldown | success | fail
    status: str
    item: Item | None = None
    points_delta: int = 0
    points_total: int = 0
    level: int = 1
    retry_at: datetime | None = None


class ClaimFindUseCase:
    """Кнопка «Сходить туда»: одна попытка на находку, кулдаун между
    походами, шанс успеха растёт с уровнем отношений. Забирает первый,
    у кого получилось; провал — небольшой штраф очков (не ниже нуля)."""

    def __init__(
        self,
        uow_factory: UowFactory,
        policy: PointsToLevelPolicy,
        cooldown_hours: int,
        fail_penalty: int,
        notes_max_chars: int = 700,
        rng: random.Random | None = None,
        settings_provider=None,
    ):
        self._uow_factory = uow_factory
        self._policy = policy
        self._cooldown_hours = cooldown_hours
        self._fail_penalty_default = fail_penalty
        self._notes_max_chars = notes_max_chars
        self._rng = rng or random.Random()
        self._settings = settings_provider

    async def execute(
        self, guild_id: int, user_id: int, message_id: int, now: datetime
    ) -> ClaimResult:
        if self._settings is not None:
            cooldown = timedelta(
                hours=self._settings.get(
                    guild_id, "finds_claim_cooldown_hours", self._cooldown_hours
                )
            )
            fail_penalty = self._settings.get(
                guild_id, "finds_fail_penalty", self._fail_penalty_default
            )
        else:
            cooldown = timedelta(hours=self._cooldown_hours)
            fail_penalty = self._fail_penalty_default
        async with self._uow_factory() as uow:
            find = await uow.night_finds.get_by_message(message_id)
            if find is None or find.id is None or not find.is_active(now):
                return ClaimResult(status="gone")
            if await uow.find_attempts.has_attempted(find.id, user_id):
                return ClaimResult(status="already")
            last = await uow.find_attempts.last_attempt_at(guild_id, user_id, "claim")
            if last is not None and now - last < cooldown:
                return ClaimResult(status="cooldown", retry_at=last + cooldown)

            profile = await uow.relationships.get_or_create(user_id, guild_id)
            level = self._policy.level(profile.points, profile.is_exclusive)
            item = get_item(find.item_id)
            if item is None:  # предмет убрали из каталога — находка мертва
                return ClaimResult(status="gone")

            if self._rng.random() < success_chance(level):
                # успех фиксируем атомарно: если кто-то успел раньше —
                # попытка не записывается и кулдаун не сгорает
                if not await uow.night_finds.claim_if_free(find.id, user_id, now):
                    return ClaimResult(status="gone")
                reward = roll_reward(self._rng, item.rarity, profile.is_exclusive)
                total = await _adjust_points(
                    uow, self._policy, guild_id, user_id, +reward, find.channel_id
                )
                await uow.collections.add(
                    CollectionItem(
                        guild_id=guild_id,
                        user_id=user_id,
                        item_id=item.id,
                        obtained_at=now,
                    )
                )
                await uow.find_attempts.add(
                    FindAttempt(
                        guild_id=guild_id,
                        user_id=user_id,
                        kind="claim",
                        success=True,
                        attempted_at=now,
                        find_id=find.id,
                    )
                )
                # легендарная находка — «она запоминает этот момент надолго»
                if item.rarity is Rarity.LEGENDARY and not profile.frozen_by_admin:
                    profile.user_notes = _append_note(
                        profile.user_notes,
                        f"Нашёл легендарную «{item.name}» ({now.date().isoformat()}) — "
                        "я запомнила этот момент.",
                        self._notes_max_chars,
                    )
                    await uow.relationships.save(profile)
                uow.add_event(
                    FindClaimed(
                        aggregate_id=str(find.id),
                        guild_id=guild_id,
                        find_id=find.id,
                        user_id=user_id,
                        item_id=item.id,
                        reward=reward,
                    )
                )
                await uow.commit()
                return ClaimResult(
                    status="success",
                    item=item,
                    points_delta=reward,
                    points_total=total,
                    level=level,
                )

            total = await _adjust_points(
                uow,
                self._policy,
                guild_id,
                user_id,
                -fail_penalty,
                find.channel_id,
            )
            await uow.find_attempts.add(
                FindAttempt(
                    guild_id=guild_id,
                    user_id=user_id,
                    kind="claim",
                    success=False,
                    attempted_at=now,
                    find_id=find.id,
                )
            )
            uow.add_event(
                FindFailed(
                    aggregate_id=str(find.id),
                    guild_id=guild_id,
                    find_id=find.id,
                    user_id=user_id,
                    penalty=fail_penalty,
                )
            )
            await uow.commit()
            return ClaimResult(
                status="fail",
                points_delta=-fail_penalty,
                points_total=total,
                level=level,
            )


@dataclass(frozen=True)
class GiftResult:
    # ok | not_owned
    status: str
    item: Item | None = None
    bonus: int = 0
    points_total: int = 0


class GiftItemUseCase:
    """/gift: предмет уходит Попосе (остаётся в истории с отметкой),
    бонус очков и тёплая строчка в её заметках о пользователе."""

    def __init__(self, uow_factory: UowFactory, policy: PointsToLevelPolicy, notes_max_chars: int):
        self._uow_factory = uow_factory
        self._policy = policy
        self._notes_max_chars = notes_max_chars

    async def execute(self, guild_id: int, user_id: int, item_id: str, now: datetime) -> GiftResult:
        async with self._uow_factory() as uow:
            entry = await uow.collections.get_ungifted(guild_id, user_id, item_id)
            item = get_item(item_id)
            if entry is None or entry.id is None or item is None:
                return GiftResult(status="not_owned")
            await uow.collections.mark_gifted(entry.id, now)
            bonus = GIFT_BONUSES[item.rarity]
            total = await _adjust_points(uow, self._policy, guild_id, user_id, +bonus)

            profile = await uow.relationships.get_or_create(user_id, guild_id)
            profile.user_notes = _append_note(
                profile.user_notes,
                f"Подарил мне «{item.name}» ({now.date().isoformat()}).",
                self._notes_max_chars,
            )
            await uow.relationships.save(profile)

            uow.add_event(
                ItemGifted(
                    aggregate_id=f"{guild_id}:{user_id}",
                    guild_id=guild_id,
                    user_id=user_id,
                    item_id=item.id,
                    bonus=bonus,
                )
            )
            await uow.commit()
            return GiftResult(status="ok", item=item, bonus=bonus, points_total=total)


@dataclass(frozen=True)
class CollectionEntry:
    item: Item
    obtained_at: datetime
    gifted_at: datetime | None


class GetCollectionUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, user_id: int) -> list[CollectionEntry]:
        async with self._uow_factory() as uow:
            rows = await uow.collections.list_for_user(guild_id, user_id)
            result: list[CollectionEntry] = []
            for row in rows:
                item = get_item(row.item_id)
                if item is not None:
                    result.append(CollectionEntry(item, row.obtained_at, row.gifted_at))
            return result


@dataclass(frozen=True)
class WalkResult:
    # cooldown | poor | success | fail
    status: str
    item: Item | None = None
    points_delta: int = 0
    points_total: int = 0
    retry_at: datetime | None = None
    cost: int = 0


class SpecialWalkUseCase:
    """Платная «специальная прогулка» раз в неделю: списывает очки и сразу
    делает бросок с повышенным шансом успеха и улучшенными весами редкости.
    Провал не штрафуется — цена прогулки уже уплачена."""

    _CHANCE_BONUS = 0.25
    _CHANCE_CAP = 0.95

    def __init__(
        self,
        uow_factory: UowFactory,
        policy: PointsToLevelPolicy,
        cost: int,
        cooldown_days: int,
        rng: random.Random | None = None,
    ):
        self._uow_factory = uow_factory
        self._policy = policy
        self._cost = cost
        self._cooldown = timedelta(days=cooldown_days)
        self._rng = rng or random.Random()

    async def execute(
        self,
        guild_id: int,
        user_id: int,
        now: datetime,
        season: str | None = None,
        holiday: str | None = None,
    ) -> WalkResult:
        async with self._uow_factory() as uow:
            last = await uow.find_attempts.last_attempt_at(guild_id, user_id, "walk")
            if last is not None and now - last < self._cooldown:
                return WalkResult(status="cooldown", retry_at=last + self._cooldown)
            profile = await uow.relationships.get_or_create(user_id, guild_id)
            if profile.points < self._cost:
                return WalkResult(status="poor", points_total=profile.points, cost=self._cost)

            level = self._policy.level(profile.points, profile.is_exclusive)
            total = await _adjust_points(uow, self._policy, guild_id, user_id, -self._cost)

            chance = min(self._CHANCE_CAP, success_chance(level) + self._CHANCE_BONUS)
            success = self._rng.random() < chance
            item: Item | None = None
            delta = -self._cost
            if success:
                item = roll_item(self._rng, season=season, boosted=True, holiday=holiday)
                reward = roll_reward(self._rng, item.rarity, profile.is_exclusive)
                total = await _adjust_points(uow, self._policy, guild_id, user_id, +reward)
                delta += reward
                await uow.collections.add(
                    CollectionItem(
                        guild_id=guild_id,
                        user_id=user_id,
                        item_id=item.id,
                        obtained_at=now,
                    )
                )
            await uow.find_attempts.add(
                FindAttempt(
                    guild_id=guild_id,
                    user_id=user_id,
                    kind="walk",
                    success=success,
                    attempted_at=now,
                )
            )
            await uow.commit()
            return WalkResult(
                status="success" if success else "fail",
                item=item,
                points_delta=delta,
                points_total=total,
                cost=self._cost,
            )


@dataclass(frozen=True)
class ActiveFindView:
    find: NightFind
    location: Location
    item: Item


class GetActiveFindUseCase:
    """/finds: активная находка сервера (одна за раз) с данными каталога."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, now: datetime) -> ActiveFindView | None:
        async with self._uow_factory() as uow:
            find = await uow.night_finds.get_active(guild_id, now)
            if find is None:
                return None
            location = get_location(find.location_id)
            item = get_item(find.item_id)
            if location is None or item is None:
                return None
            return ActiveFindView(find=find, location=location, item=item)


class ListLiveFindsUseCase:
    """Живые находки всех гильдий — восстановление после рестарта бота."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, now: datetime) -> list[NightFind]:
        async with self._uow_factory() as uow:
            return await uow.night_finds.list_unclaimed(now)


@dataclass(frozen=True)
class CollectorStat:
    user_id: int
    total: int
    gifted: int


class TopCollectorsUseCase:
    """Топ коллекционеров сервера: у кого больше всего находок (и сколько
    подарено Попосе). Для read-обзора находок в панели."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, limit: int = 20) -> list[CollectorStat]:
        async with self._uow_factory() as uow:
            rows = await uow.collections.top_collectors(guild_id, limit)
            return [CollectorStat(user_id=uid, total=tot, gifted=gif) for uid, tot, gif in rows]
