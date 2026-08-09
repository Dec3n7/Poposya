"""EntitlementService: выдача/снятие подписки, срок, тариф по умолчанию,
кэш и перечитывание — поверх реального SQLite."""

from datetime import UTC, datetime, timedelta

import pytest

from src.application.interfaces.entitlements import PlanTier
from src.config import Settings
from src.infrastructure.entitlements import EntitlementService, parse_tier


def make_settings(**over):
    return Settings(_env_file=None, discord_token="t", **over)


@pytest.fixture
async def service(session_factory):
    svc = EntitlementService(make_settings(), session_factory)
    await svc.load_all()
    return svc


def test_parse_tier():
    assert parse_tier("free") is PlanTier.FREE
    assert parse_tier("PREMIUM") is PlanTier.PREMIUM
    assert parse_tier(" pro ") is PlanTier.PRO
    with pytest.raises(ValueError):
        parse_tier("platinum")


async def test_default_tier_when_no_row(service):
    # нет строки -> тариф по умолчанию из настроек (агностично к значению)
    assert service.tier(123) is service.default_tier
    tier, expires, active = service.current(123)
    assert (tier, expires, active) == (service.default_tier, None, False)


async def test_default_tier_is_free_by_default():
    # дефолт конфига — free (enforcement включён из коробки)
    from src.config import Settings

    assert Settings(_env_file=None, discord_token="t").entitlements_default_tier == "free"


async def test_default_tier_free_enforced(session_factory):
    svc = EntitlementService(make_settings(entitlements_default_tier="free"), session_factory)
    await svc.load_all()
    assert svc.tier(1) is PlanTier.FREE
    assert svc.default_tier is PlanTier.FREE


async def test_grant_permanent(service):
    await service.grant(7, PlanTier.PREMIUM, None, granted_by=99)
    assert service.tier(7) is PlanTier.PREMIUM
    tier, expires, active = service.current(7)
    assert tier is PlanTier.PREMIUM and expires is None and active is True


async def test_grant_with_future_expiry_active(service):
    await service.grant(8, PlanTier.PRO, datetime.now(UTC) + timedelta(days=30), granted_by=1)
    assert service.tier(8) is PlanTier.PRO
    _, expires, active = service.current(8)
    assert active is True and expires is not None


async def test_expired_grant_falls_back_to_default(service):
    await service.grant(9, PlanTier.PREMIUM, datetime.now(UTC) - timedelta(seconds=1), granted_by=1)
    # истекла -> тариф по умолчанию (PRO), active=False
    assert service.tier(9) is service.default_tier
    tier, _, active = service.current(9)
    assert active is False and tier is service.default_tier


async def test_grant_upsert_overwrites(service):
    await service.grant(10, PlanTier.PREMIUM, None, granted_by=1)
    await service.grant(10, PlanTier.FREE, None, granted_by=2)
    assert service.tier(10) is PlanTier.FREE


async def test_revoke(service):
    await service.grant(11, PlanTier.PREMIUM, None, granted_by=1)
    assert await service.revoke(11) is True
    assert service.tier(11) is service.default_tier
    assert await service.revoke(11) is False  # уже нет


async def test_persistence_across_reload(session_factory):
    svc = EntitlementService(make_settings(), session_factory)
    await svc.grant(12, PlanTier.PREMIUM, None, granted_by=1)
    # новый инстанс на той же БД видит подписку после load_all
    fresh = EntitlementService(make_settings(), session_factory)
    await fresh.load_all()
    assert fresh.tier(12) is PlanTier.PREMIUM


async def test_reload_guild_picks_up_change(session_factory):
    reader = EntitlementService(make_settings(), session_factory)
    await reader.load_all()
    assert reader.tier(13) is reader.default_tier
    writer = EntitlementService(make_settings(), session_factory)
    await writer.grant(13, PlanTier.PREMIUM, None, granted_by=1)
    # reader не знает, пока не перечитает (эмуляция NOTIFY)
    await reader.reload_guild(13)
    assert reader.tier(13) is PlanTier.PREMIUM
