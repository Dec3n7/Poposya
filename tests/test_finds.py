import random
from datetime import UTC, datetime, timedelta

import pytest

from src.application.finds.use_cases import (
    ClaimFindUseCase,
    GiftItemUseCase,
    SpawnFindUseCase,
    SpecialWalkUseCase,
)
from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.events.base import DomainEvent
from src.domain.finds import catalog
from src.domain.finds.entities import CollectionItem, FindAttempt, NightFind, Rarity
from src.domain.finds.repository import (
    ICollectionRepository,
    IFindAttemptRepository,
    INightFindRepository,
)
from src.domain.relationship.entities import RelationshipProfile
from src.domain.relationship.policies import PointsToLevelPolicy
from src.domain.relationship.repository import IRelationshipRepository

NOW = datetime(2026, 7, 10, 21, 0, tzinfo=UTC)


class ForcedRng(random.Random):
    """random() всегда возвращает заданное значение — управляем исходом броска."""

    def __init__(self, value: float):
        super().__init__(0)
        self._value = value

    def random(self):
        return self._value


class FakeRelationships(IRelationshipRepository):
    def __init__(self):
        self.profiles: dict[tuple[int, int], RelationshipProfile] = {}

    async def get(self, user_id, guild_id):
        return self.profiles.get((user_id, guild_id))

    async def get_or_create(self, user_id, guild_id):
        key = (user_id, guild_id)
        if key not in self.profiles:
            self.profiles[key] = RelationshipProfile(user_id=user_id, guild_id=guild_id)
        return self.profiles[key]

    async def get_exclusive_holder(self, guild_id):
        for profile in self.profiles.values():
            if profile.guild_id == guild_id and profile.is_exclusive:
                return profile
        return None

    async def find_birthdays(self, month, day):
        return []

    async def top_by_points(self, guild_id, limit):
        return []

    async def list_decayable(self, inactive_before, decayed_before):
        return []

    async def save(self, profile):
        self.profiles[(profile.user_id, profile.guild_id)] = profile


class FakeNightFinds(INightFindRepository):
    def __init__(self):
        self.rows: dict[int, NightFind] = {}
        self._seq = 0

    async def add(self, find):
        self._seq += 1
        find.id = self._seq
        self.rows[find.id] = find
        return find

    async def save(self, find):
        self.rows[find.id] = find

    async def get(self, find_id):
        return self.rows.get(find_id)

    async def get_by_message(self, message_id):
        return next((f for f in self.rows.values() if f.message_id == message_id), None)

    async def get_active(self, guild_id, now):
        return next(
            (f for f in self.rows.values() if f.guild_id == guild_id and f.is_active(now)),
            None,
        )

    async def list_unclaimed(self, now):
        return [f for f in self.rows.values() if f.is_active(now)]

    async def claim_if_free(self, find_id, user_id, now):
        find = self.rows[find_id]
        if find.claimed_by is not None:
            return False
        find.claimed_by = user_id
        find.claimed_at = now
        return True


class FakeCollections(ICollectionRepository):
    def __init__(self):
        self.items: list[CollectionItem] = []
        self._seq = 0

    async def add(self, item):
        self._seq += 1
        self.items.append(
            CollectionItem(
                id=self._seq,
                guild_id=item.guild_id,
                user_id=item.user_id,
                item_id=item.item_id,
                obtained_at=item.obtained_at,
                gifted_at=item.gifted_at,
            )
        )

    async def list_for_user(self, guild_id, user_id):
        return [i for i in self.items if i.guild_id == guild_id and i.user_id == user_id]

    async def get_ungifted(self, guild_id, user_id, item_id):
        return next(
            (
                i
                for i in self.items
                if i.guild_id == guild_id
                and i.user_id == user_id
                and i.item_id == item_id
                and i.gifted_at is None
            ),
            None,
        )

    async def mark_gifted(self, collection_item_id, now):
        for idx, item in enumerate(self.items):
            if item.id == collection_item_id:
                self.items[idx] = CollectionItem(
                    id=item.id,
                    guild_id=item.guild_id,
                    user_id=item.user_id,
                    item_id=item.item_id,
                    obtained_at=item.obtained_at,
                    gifted_at=now,
                )


class FakeAttempts(IFindAttemptRepository):
    def __init__(self):
        self.attempts: list[FindAttempt] = []

    async def add(self, attempt):
        self.attempts.append(attempt)

    async def last_attempt_at(self, guild_id, user_id, kind):
        matching = [
            a.attempted_at
            for a in self.attempts
            if a.guild_id == guild_id and a.user_id == user_id and a.kind == kind
        ]
        return max(matching) if matching else None

    async def has_attempted(self, find_id, user_id):
        return any(a.find_id == find_id and a.user_id == user_id for a in self.attempts)


class FakeUoW(IUnitOfWork):
    def __init__(self, state, published: list[DomainEvent]):
        self.relationships = state["relationships"]
        self.night_finds = state["night_finds"]
        self.collections = state["collections"]
        self.find_attempts = state["find_attempts"]
        self._published = published
        self._pending: list[DomainEvent] = []

    async def __aenter__(self):
        self._pending = []
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    def add_event(self, event):
        self._pending.append(event)

    async def commit(self):
        self._published.extend(self._pending)
        self._pending = []

    async def rollback(self):
        self._pending = []


@pytest.fixture
def state():
    return {
        "relationships": FakeRelationships(),
        "night_finds": FakeNightFinds(),
        "collections": FakeCollections(),
        "find_attempts": FakeAttempts(),
    }


@pytest.fixture
def events():
    return []


@pytest.fixture
def uow_factory(state, events):
    return lambda: FakeUoW(state, events)


@pytest.fixture
def policy():
    return PointsToLevelPolicy()


async def _make_find(state, message_id=111, item_id="zippo_engraved") -> NightFind:
    return await state["night_finds"].add(
        NightFind(
            guild_id=10,
            location_id="nezu_square",
            item_id=item_id,
            created_at=NOW,
            expires_at=NOW + timedelta(hours=12),
            channel_id=5,
            message_id=message_id,
        )
    )


def _claim_uc(uow_factory, policy, rng_value: float) -> ClaimFindUseCase:
    return ClaimFindUseCase(
        uow_factory,
        policy,
        cooldown_hours=8,
        fail_penalty=5,
        notes_max_chars=700,
        rng=ForcedRng(rng_value),
    )


# --- каталог ---


def test_catalog_counts_match_spec():
    by_rarity = {rarity: 0 for rarity in Rarity}
    for item in catalog.ITEMS:
        if item.holiday is None:  # праздничные — сверх базового каталога
            by_rarity[item.rarity] += 1
    assert by_rarity[Rarity.COMMON] == 8
    assert by_rarity[Rarity.UNCOMMON] == 10
    assert by_rarity[Rarity.RARE] == 6
    assert by_rarity[Rarity.LEGENDARY] == 3
    assert len(catalog.LOCATIONS) == 15


def test_catalog_ids_unique_and_seasons_valid():
    item_ids = [item.id for item in catalog.ITEMS]
    assert len(item_ids) == len(set(item_ids))
    loc_ids = [loc.id for loc in catalog.LOCATIONS]
    assert len(loc_ids) == len(set(loc_ids))
    valid = {None, "весна", "лето", "осень", "зима"}
    assert all(item.season in valid for item in catalog.ITEMS)
    # праздничные метки — строго "ДД-ММ"
    import re

    for item in catalog.ITEMS:
        if item.holiday is not None:
            assert re.fullmatch(r"\d{2}-\d{2}", item.holiday), item.id


def test_holiday_items_only_drop_on_their_day():
    rng = random.Random(3)
    holiday_ids = {item.id for item in catalog.ITEMS if item.holiday is not None}
    # в обычный день праздничные предметы не выпадают никогда
    for _ in range(500):
        assert catalog.roll_item(rng, holiday=None).id not in holiday_ids
    # в свой день предмет выпадает (вес x4 при boosted-весах)
    seen = set()
    for _ in range(500):
        seen.add(catalog.roll_item(rng, boosted=True, holiday="02-06").id)
    assert "poposya_polaroid" in seen
    # чужой праздник не подмешивается
    for _ in range(500):
        item = catalog.roll_item(rng, holiday="31-10")
        assert item.holiday in (None, "31-10")


def test_roll_reward_ranges():
    rng = random.Random(7)
    for rarity, (low, high) in catalog.REWARD_RANGES.items():
        for _ in range(50):
            assert low <= catalog.roll_reward(rng, rarity) <= high
    # бонус «Единственного» +10–20 сверху
    low, high = catalog.REWARD_RANGES[Rarity.COMMON]
    for _ in range(50):
        reward = catalog.roll_reward(rng, Rarity.COMMON, exclusive_bonus=True)
        assert low + 10 <= reward <= high + 20


def test_success_chance_grows_with_level():
    chances = [catalog.success_chance(level) for level in range(1, 8)]
    assert chances == sorted(chances)
    assert chances[0] == 0.45
    assert chances[-1] == 0.70


# --- claim ---


async def test_claim_success_awards_and_collects(state, events, uow_factory, policy):
    find = await _make_find(state)
    uc = _claim_uc(uow_factory, policy, rng_value=0.0)  # всегда успех
    result = await uc.execute(10, 1, 111, NOW)

    assert result.status == "success"
    assert result.item.id == "zippo_engraved"
    low, high = catalog.REWARD_RANGES[Rarity.RARE]
    assert low <= result.points_delta <= high
    profile = state["relationships"].profiles[(1, 10)]
    assert profile.points == result.points_delta
    assert find.claimed_by == 1
    assert len(state["collections"].items) == 1
    assert state["find_attempts"].attempts[0].success


async def test_claim_is_first_come_first_served(state, uow_factory, policy, events):
    await _make_find(state)
    uc = _claim_uc(uow_factory, policy, rng_value=0.0)
    first = await uc.execute(10, 1, 111, NOW)
    second = await uc.execute(10, 2, 111, NOW)
    assert first.status == "success"
    assert second.status == "gone"


async def test_claim_fail_penalty_not_below_zero(state, uow_factory, policy, events):
    await _make_find(state)
    uc = _claim_uc(uow_factory, policy, rng_value=0.99)  # всегда провал
    result = await uc.execute(10, 1, 111, NOW)
    assert result.status == "fail"
    assert result.points_delta == -5
    # очков не было — в минус не уходим
    assert state["relationships"].profiles[(1, 10)].points == 0


async def test_claim_cooldown_between_hunts(state, uow_factory, policy, events):
    await _make_find(state, message_id=111)
    await _make_find(state, message_id=222)
    uc = _claim_uc(uow_factory, policy, rng_value=0.99)
    await uc.execute(10, 1, 111, NOW)
    result = await uc.execute(10, 1, 222, NOW + timedelta(hours=1))
    assert result.status == "cooldown"
    assert result.retry_at == NOW + timedelta(hours=8)
    # после кулдауна — можно
    result = await uc.execute(10, 1, 222, NOW + timedelta(hours=9))
    assert result.status == "fail"


async def test_claim_one_attempt_per_find(state, uow_factory, policy, events):
    await _make_find(state)
    uc = _claim_uc(uow_factory, policy, rng_value=0.99)
    await uc.execute(10, 1, 111, NOW)
    result = await uc.execute(10, 1, 111, NOW + timedelta(hours=9))
    assert result.status == "already"


async def test_claim_expired_find_is_gone(state, uow_factory, policy, events):
    await _make_find(state)
    uc = _claim_uc(uow_factory, policy, rng_value=0.0)
    result = await uc.execute(10, 1, 111, NOW + timedelta(hours=13))
    assert result.status == "gone"


async def test_legendary_claim_writes_note(state, uow_factory, policy, events):
    await _make_find(state, item_id="unsent_letter")
    uc = _claim_uc(uow_factory, policy, rng_value=0.0)
    result = await uc.execute(10, 1, 111, NOW)
    assert result.status == "success"
    assert "легендарную" in state["relationships"].profiles[(1, 10)].user_notes


# --- gift ---


async def test_gift_awards_bonus_and_notes(state, uow_factory, policy, events):
    await state["collections"].add(
        CollectionItem(
            guild_id=10,
            user_id=1,
            item_id="butterfly_pin",
            obtained_at=NOW,
        )
    )
    uc = GiftItemUseCase(uow_factory, policy, notes_max_chars=700)
    result = await uc.execute(10, 1, "butterfly_pin", NOW)
    assert result.status == "ok"
    assert result.bonus == catalog.GIFT_BONUSES[Rarity.UNCOMMON]
    profile = state["relationships"].profiles[(1, 10)]
    assert profile.points == result.bonus
    assert "Подарил мне" in profile.user_notes
    # повторно тот же предмет не подарить
    result = await uc.execute(10, 1, "butterfly_pin", NOW)
    assert result.status == "not_owned"


# --- специальная прогулка ---


async def test_walk_requires_points(state, uow_factory, policy, events):
    uc = SpecialWalkUseCase(uow_factory, policy, cost=60, cooldown_days=7)
    result = await uc.execute(10, 1, NOW)
    assert result.status == "poor"


async def test_walk_spends_rewards_and_cools_down(state, uow_factory, policy, events):
    profile = await state["relationships"].get_or_create(1, 10)
    profile.points = 200
    uc = SpecialWalkUseCase(uow_factory, policy, cost=60, cooldown_days=7, rng=ForcedRng(0.0))
    result = await uc.execute(10, 1, NOW)
    assert result.status == "success"
    assert result.item is not None
    assert profile.points == 200 + result.points_delta
    assert len(state["collections"].items) == 1
    # раз в неделю
    result = await uc.execute(10, 1, NOW + timedelta(days=2))
    assert result.status == "cooldown"
    result = await uc.execute(10, 1, NOW + timedelta(days=8))
    assert result.status in ("success", "fail")


# --- спавн ---


async def test_spawn_only_one_active_per_guild(state, uow_factory, events):
    uc = SpawnFindUseCase(uow_factory, lifetime_hours=12, rng=random.Random(1))
    first = await uc.execute(10, NOW)
    assert first is not None
    find, location, item = first
    assert find.id is not None
    assert catalog.get_location(location.id) is location
    assert catalog.get_item(item.id) is item
    # вторая — не создаётся, пока жива первая
    assert await uc.execute(10, NOW) is None
    # в другой гильдии — своя находка
    assert await uc.execute(20, NOW) is not None
    # после протухания первой можно снова
    assert await uc.execute(10, NOW + timedelta(hours=13)) is not None
