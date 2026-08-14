"""PremiumKeyService: выпуск, активация (сит-логика + атомарный grant), отзыв
soft/hard, реактивация, rate-limit, инвентарь и перевыпуск ключей.

Проверяем контракт docs/plans/premium-keys.md §3–§4: сит и Premium выдаются
атомарно, мультисит уважает cap, отзыв блокирует активацию (soft) и снимает
выданное (hard), а перевыпуск из реестра совпадает с исходным ключом.
"""

import pytest

from src.application.interfaces.entitlements import PlanTier
from src.config import Settings
from src.infrastructure.entitlements import EntitlementService
from src.infrastructure.premium_keys import codec
from src.infrastructure.premium_keys.service import PremiumKeyService, RedeemOutcome

SECRET = "svc-signing-secret-not-for-production-0123456789"
OPERATOR = 1000


def _settings(**over):
    return Settings(_env_file=None, discord_token="t", **over)


def _service(session_factory, *, attempts_per_hour=100, shelf_life_days=730):
    ent = EntitlementService(_settings(), session_factory)
    svc = PremiumKeyService(
        session_factory,
        ent,
        SECRET,
        attempts_per_hour=attempts_per_hour,
        shelf_life_days=shelf_life_days,
    )
    return svc, ent


async def _mint(svc, tier=PlanTier.PREMIUM, duration=30, count=1, label="b"):
    return await svc.mint_batch(
        tier=tier, duration_days=duration, count=count, label=label, created_by=OPERATOR
    )


# ── выпуск ───────────────────────────────────────────────────────────────────


async def test_mint_produces_valid_keys_and_registry(session_factory):
    svc, _ = _service(session_factory)
    batch = await _mint(svc, tier=PlanTier.PRO, duration=90, count=3)
    assert batch.tier is PlanTier.PRO and batch.seats == 5 and len(batch.keys) == 3
    for key in batch.keys:
        p = codec.verify(SECRET, key)
        assert p is not None and p.tier is PlanTier.PRO
        assert p.duration_days == 90 and p.batch_id == batch.batch_id


async def test_mint_disabled_without_secret(session_factory):
    ent = EntitlementService(_settings(), session_factory)
    svc = PremiumKeyService(session_factory, ent, "")
    assert svc.enabled is False
    with pytest.raises(RuntimeError):
        await _mint(svc)


# ── активация: базовый путь + атомарный grant ────────────────────────────────


async def test_redeem_grants_premium(session_factory):
    svc, ent = _service(session_factory)
    key = (await _mint(svc, tier=PlanTier.PREMIUM, duration=30)).keys[0]
    res = await svc.redeem(key, guild_id=500, user_id=7)
    assert res.outcome is RedeemOutcome.OK and res.ok
    assert res.tier is PlanTier.PREMIUM
    assert ent.tier(500) is PlanTier.PREMIUM  # грант применён и кэш обновлён


async def test_reactivation_same_guild_extends_without_spending_seat(session_factory):
    svc, ent = _service(session_factory)
    key = (await _mint(svc, tier=PlanTier.PREMIUM, duration=30)).keys[0]
    first = await svc.redeem(key, 500, 7)
    second = await svc.redeem(key, 500, 7)  # тот же сервер
    assert second.outcome is RedeemOutcome.EXTENDED
    assert second.expires_at > first.expires_at  # срок стакается (§6)
    assert second.seats_used == 1  # сит не потрачен второй раз


async def test_premium_seat_cap_full_on_second_guild(session_factory):
    svc, _ = _service(session_factory)
    key = (await _mint(svc, tier=PlanTier.PREMIUM)).keys[0]  # seats=1
    assert (await svc.redeem(key, 1, 7)).outcome is RedeemOutcome.OK
    full = await svc.redeem(key, 2, 8)  # другой сервер — слот занят
    assert full.outcome is RedeemOutcome.FULL and full.seats_total == 1


async def test_pro_multiseat_five_then_full(session_factory):
    svc, ent = _service(session_factory)
    key = (await _mint(svc, tier=PlanTier.PRO)).keys[0]  # seats=5
    for gid in range(10, 15):
        assert (await svc.redeem(key, gid, 7)).outcome is RedeemOutcome.OK
        assert ent.tier(gid) is PlanTier.PRO
    assert (await svc.redeem(key, 99, 7)).outcome is RedeemOutcome.FULL  # 6-й


# ── отзыв партии (§3a) ───────────────────────────────────────────────────────


async def test_soft_revoke_blocks_future_activation(session_factory):
    svc, _ = _service(session_factory)
    batch = await _mint(svc)
    await svc.revoke_batch(batch.batch_id, revoked_by=OPERATOR, reason="leak")
    res = await svc.redeem(batch.keys[0], 500, 7)
    assert res.outcome is RedeemOutcome.REVOKED


async def test_soft_revoke_keeps_already_activated(session_factory):
    svc, ent = _service(session_factory)
    batch = await _mint(svc)
    await svc.redeem(batch.keys[0], 500, 7)  # активирован ДО отзыва
    await svc.revoke_batch(batch.batch_id, revoked_by=OPERATOR, reason="promo end")
    assert ent.tier(500) is PlanTier.PREMIUM  # soft не трогает выданное


async def test_hard_revoke_strips_active_entitlements(session_factory):
    svc, ent = _service(session_factory)
    batch = await _mint(svc, tier=PlanTier.PRO, count=1)  # seats=5
    await svc.redeem(batch.keys[0], 500, 7)
    await svc.redeem(batch.keys[0], 501, 7)
    result = await svc.revoke_batch(batch.batch_id, revoked_by=OPERATOR, reason="leak", hard=True)
    assert result.guilds_stripped == 2
    assert ent.tier(500) is PlanTier.FREE and ent.tier(501) is PlanTier.FREE


async def test_reactivate_restores_activation(session_factory):
    svc, _ = _service(session_factory)
    batch = await _mint(svc)
    await svc.revoke_batch(batch.batch_id, revoked_by=OPERATOR, reason="x")
    assert await svc.reactivate_batch(batch.batch_id) is True
    assert (await svc.redeem(batch.keys[0], 500, 7)).outcome is RedeemOutcome.OK


# ── verify-исходы и rate-limit (§4) ──────────────────────────────────────────


async def test_invalid_key(session_factory):
    svc, _ = _service(session_factory)
    assert (await svc.redeem("POPO-GARBAGE-XXXX", 500, 7)).outcome is RedeemOutcome.INVALID


async def test_expired_key(session_factory):
    svc, _ = _service(session_factory, shelf_life_days=-1)  # ключ протух вчера
    batch = await _mint(svc)
    assert (await svc.redeem(batch.keys[0], 500, 7)).outcome is RedeemOutcome.EXPIRED


async def test_rate_limit_after_threshold(session_factory):
    svc, _ = _service(session_factory, attempts_per_hour=2)
    for _ in range(2):
        await svc.redeem("POPO-BAD-KEYY", 500, 7)  # две попытки (провальные, но считаются)
    third = await svc.redeem("POPO-BAD-KEYY", 500, 7)
    assert third.outcome is RedeemOutcome.RATE_LIMITED


async def test_rate_limit_per_user_not_shared(session_factory):
    svc, _ = _service(session_factory, attempts_per_hour=1)
    await svc.redeem("POPO-BAD-KEYY", 500, 7)  # исчерпал юзер 7
    assert (await svc.redeem("POPO-BAD-KEYY", 500, 7)).outcome is RedeemOutcome.RATE_LIMITED
    # другой пользователь не задет
    assert (await svc.redeem("POPO-BAD-KEYY", 500, 8)).outcome is RedeemOutcome.INVALID


# ── инвентарь и перевыпуск для панели ────────────────────────────────────────


async def test_batch_keys_remint_matches_original(session_factory):
    svc, _ = _service(session_factory)
    batch = await _mint(svc, count=3)
    views = await svc.batch_keys(batch.batch_id)
    assert sorted(v.key for v in views) == sorted(batch.keys)  # перевыпуск == исходные
    assert all(v.status == "unredeemed" for v in views)


async def test_inventory_counts_and_status(session_factory):
    svc, _ = _service(session_factory)
    batch = await _mint(svc, tier=PlanTier.PRO, count=2)  # 2 ключа × 5 ситов = 10 слотов
    await svc.redeem(batch.keys[0], 500, 7)  # 1 сит потрачен
    batches = await svc.list_batches()
    assert len(batches) == 1
    b = batches[0]
    assert b.issued == 2 and b.capacity == 10 and b.redeemed_seats == 1
    keys = await svc.batch_keys(batch.batch_id)
    statuses = sorted(k.status for k in keys)
    assert statuses == ["partial", "unredeemed"]  # один тронут (1/5), другой нет


async def test_export_unredeemed_only(session_factory):
    svc, _ = _service(session_factory)
    batch = await _mint(svc, count=3)
    await svc.redeem(batch.keys[0], 500, 7)
    unredeemed = await svc.export_batch(batch.batch_id, only_unredeemed=True)
    assert len(unredeemed) == 2 and batch.keys[0] not in unredeemed


async def test_sku_inventory_groups_by_tier_duration(session_factory):
    svc, _ = _service(session_factory)
    await _mint(svc, tier=PlanTier.PREMIUM, duration=30, count=2)
    await _mint(svc, tier=PlanTier.PREMIUM, duration=30, count=1)  # тот же SKU
    await _mint(svc, tier=PlanTier.PRO, duration=365, count=1)
    skus = await svc.sku_inventory()
    prem30 = next(s for s in skus if s.tier is PlanTier.PREMIUM and s.duration_days == 30)
    assert prem30.issued == 3 and prem30.remaining == 3  # 3 ключа premium=1 сит
    pro365 = next(s for s in skus if s.tier is PlanTier.PRO and s.duration_days == 365)
    assert pro365.issued == 1 and pro365.capacity == 5
