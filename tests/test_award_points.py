from datetime import datetime, timedelta, timezone

import pytest

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.application.relationship.use_cases import AwardPointUseCase
from src.domain.events.base import DomainEvent
from src.domain.relationship.entities import RelationshipProfile
from src.domain.relationship.events import ExclusiveTransferred, RelationshipRoleChanged
from src.domain.relationship.policies import PointsToLevelPolicy
from src.domain.relationship.repository import IRelationshipRepository

NOW = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)


class FakeRepo(IRelationshipRepository):
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
        return [
            p for p in self.profiles.values()
            if p.birthday_month == month and p.birthday_day == day
        ]

    async def top_by_points(self, guild_id, limit):
        rows = [p for p in self.profiles.values() if p.guild_id == guild_id and p.points > 0]
        return sorted(rows, key=lambda p: p.points, reverse=True)[:limit]

    async def list_decayable(self, inactive_before, decayed_before):
        return []

    async def save(self, profile):
        self.profiles[(profile.user_id, profile.guild_id)] = profile


class FakeSummaries:
    async def add(self, guild_id, user_id, summary, at, keep):
        pass

    async def last(self, guild_id, user_id, limit):
        return []


class FakeUoW(IUnitOfWork):
    def __init__(self, repo: FakeRepo, published: list[DomainEvent]):
        self.relationships = repo
        self.dialog_summaries = FakeSummaries()
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
def repo():
    return FakeRepo()


@pytest.fixture
def events():
    return []


@pytest.fixture
def use_case(repo, events):
    return AwardPointUseCase(
        uow_factory=lambda: FakeUoW(repo, events),
        policy=PointsToLevelPolicy(),
        daily_cap=20,
        absence_days=30,
    )


async def test_first_message_awards_point(use_case):
    result = await use_case.execute(1, 10, 100, NOW)
    assert result.point_awarded
    assert result.points == 1
    assert result.level == 1
    assert result.role_index is None


async def test_daily_cap_stops_awarding(use_case):
    for _ in range(25):
        result = await use_case.execute(1, 10, 100, NOW)
    assert result.points == 20
    assert not result.point_awarded
    # на следующий день счётчик сбрасывается
    result = await use_case.execute(1, 10, 100, NOW + timedelta(days=1))
    assert result.point_awarded
    assert result.points == 21


async def test_voice_base_amount_respects_daily_cap(use_case):
    # +3 за час войса; общий дневной потолок 20 останавливает фарм
    for _ in range(6):
        result = await use_case.execute(1, 10, 0, NOW, base_amount=3)
    assert result.points == 18
    assert result.point_awarded
    result = await use_case.execute(1, 10, 0, NOW, base_amount=3)
    assert result.points == 21  # последнее начисление чуть выше капа — как у праздников
    result = await use_case.execute(1, 10, 0, NOW, base_amount=3)
    assert not result.point_awarded
    assert result.points == 21


async def test_role_change_publishes_event(use_case, repo, events):
    profile = await repo.get_or_create(1, 10)
    profile.points = 99
    result = await use_case.execute(1, 10, 100, NOW)
    assert result.points == 100
    assert result.role_index == 0
    role_events = [e for e in events if isinstance(e, RelationshipRoleChanged)]
    assert len(role_events) == 1
    assert role_events[0].new_role_index == 0


async def test_first_to_exclusive_threshold_becomes_exclusive(use_case, repo, events):
    profile = await repo.get_or_create(1, 10)
    profile.points = 1249
    result = await use_case.execute(1, 10, 100, NOW)
    assert result.is_exclusive
    assert result.became_exclusive
    assert result.level == 7
    transfers = [e for e in events if isinstance(e, ExclusiveTransferred)]
    assert len(transfers) == 1
    assert transfers[0].previous_user_id is None


async def test_exclusive_transfers_only_on_strict_overtake(use_case, repo, events):
    holder = await repo.get_or_create(1, 10)
    holder.points = 1400
    holder.is_exclusive = True

    challenger = await repo.get_or_create(2, 10)
    challenger.points = 1399
    # 1399 -> 1400: равенство, титул НЕ переходит
    result = await use_case.execute(2, 10, 100, NOW)
    assert result.points == 1400
    assert not result.is_exclusive
    assert holder.is_exclusive

    # 1400 -> 1401: строгое превышение, титул переходит
    result = await use_case.execute(2, 10, 100, NOW)
    assert result.points == 1401
    assert result.is_exclusive
    assert result.became_exclusive
    assert not holder.is_exclusive
    transfers = [e for e in events if isinstance(e, ExclusiveTransferred)]
    assert len(transfers) == 1
    assert transfers[0].previous_user_id == 1
    assert transfers[0].new_user_id == 2


async def test_frozen_profile_gains_nothing(use_case, repo):
    profile = await repo.get_or_create(1, 10)
    profile.frozen_by_admin = True
    result = await use_case.execute(1, 10, 100, NOW)
    assert not result.point_awarded
    assert result.points == 0
    # но дата диалога обновилась — «возвращение» считается от неё
    assert profile.last_dialog_at == NOW


async def test_returning_after_absence_flag(use_case, repo):
    profile = await repo.get_or_create(1, 10)
    profile.points = 100
    profile.last_dialog_at = NOW - timedelta(days=45)
    result = await use_case.execute(1, 10, 100, NOW)
    assert result.returning_after_absence
    # следующее сообщение — уже без флага
    result = await use_case.execute(1, 10, 100, NOW + timedelta(minutes=5))
    assert not result.returning_after_absence
