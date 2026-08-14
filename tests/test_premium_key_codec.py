"""Кодек лицензионных ключей: подпись/подделка, SKU-валидация, роундтрип полей.

Проверяем контракт из docs/plans/premium-keys.md §1: ключ самоподписан, любая
правка ломает подпись, поля (tier/seats/duration/expiry/batch/nonce) переживают
mint→verify, а перевыпуск из тех же полей детерминирован (основа реестра §2).
"""

from datetime import date, timedelta

import pytest

from src.application.interfaces.entitlements import PlanTier
from src.infrastructure.premium_keys import codec

SECRET = "test-signing-secret-not-for-production-0123456789"
EXPIRY = date(2027, 1, 1)


def _mint(**kw):
    base = dict(tier=PlanTier.PREMIUM, duration_days=30, key_expiry=EXPIRY, batch_id=42)
    return codec.mint(SECRET, **{**base, **kw})


def test_roundtrip_preserves_all_fields():
    key = _mint(tier=PlanTier.PRO, duration_days=365, batch_id=7, nonce=0xABCDEF0123456789)
    p = codec.verify(SECRET, key)
    assert p is not None
    assert p.tier is PlanTier.PRO
    assert p.seats == 5  # pro
    assert p.duration_days == 365
    assert p.key_expiry == EXPIRY
    assert p.batch_id == 7
    assert p.nonce == 0xABCDEF0123456789
    assert p.nonce_hex == "abcdef0123456789"


def test_key_shape():
    key = _mint()
    assert key.startswith("POPO-")
    assert key.count("-") == 2
    # base32-алфавит: заглавные A–Z и 2–7, без 0/1/8/9/дефисов внутри частей
    for part in key.split("-")[1:]:
        assert part and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in part)


def test_default_seats_by_tier():
    assert codec.verify(SECRET, _mint(tier=PlanTier.PREMIUM)).seats == 1
    assert codec.verify(SECRET, _mint(tier=PlanTier.PRO)).seats == 5


def test_deterministic_remint_same_nonce():
    # тот же payload (в т.ч. nonce) + тот же секрет → тот же ключ (перевыпуск §2)
    a = _mint(nonce=123456789)
    b = _mint(nonce=123456789)
    assert a == b


def test_random_nonce_differs():
    assert _mint() != _mint()  # без явного nonce — свежие 64 бита


@pytest.mark.parametrize("bad_secret", ["wrong-secret", "", SECRET + "x"])
def test_wrong_secret_rejected(bad_secret):
    key = _mint()
    assert codec.verify(bad_secret, key) is None


def test_tamper_any_char_breaks_signature():
    key = _mint(nonce=1)
    body = key.split("-")[1]
    # заменить один символ в payload-части на другой из алфавита
    idx = 3
    swapped = "Q" if body[idx] != "Q" else "R"
    tampered = key.replace(body, body[:idx] + swapped + body[idx + 1 :], 1)
    assert tampered != key
    assert codec.verify(SECRET, tampered) is None


def test_truncated_and_garbage_return_none_not_raise():
    for bad in ["", "POPO", "POPO-", "POPO-AAAA", "not-a-key", "POPO-!!!-###", "X-Y-Z"]:
        assert codec.verify(SECRET, bad) is None


def test_case_insensitive_and_whitespace_tolerant():
    key = _mint(nonce=99)
    assert codec.verify(SECRET, f"  {key.lower()}  ") is not None


@pytest.mark.parametrize("dur", [0, 1, 45, 31, 360, 366, 730])
def test_mint_rejects_non_sku_duration(dur):
    with pytest.raises(ValueError, match="duration_days"):
        _mint(duration_days=dur)


@pytest.mark.parametrize("dur", codec.KEY_DURATIONS)
def test_mint_accepts_sku_durations(dur):
    assert codec.verify(SECRET, _mint(duration_days=dur)).duration_days == dur


def test_mint_rejects_free_tier():
    with pytest.raises(ValueError, match="free|FREE"):
        _mint(tier=PlanTier.FREE)


def test_mint_rejects_out_of_range_batch_and_expiry():
    with pytest.raises(ValueError, match="batch_id"):
        _mint(batch_id=2**32)
    with pytest.raises(ValueError, match="key_expiry"):
        _mint(key_expiry=date(2000, 1, 1))  # раньше эпохи → отрицательные дни


def test_expiry_roundtrip_and_is_expired():
    key = _mint(key_expiry=date(2026, 6, 15))
    p = codec.verify(SECRET, key)
    assert p.key_expiry == date(2026, 6, 15)
    assert codec.is_expired(p, date(2026, 6, 16)) is True
    assert codec.is_expired(p, date(2026, 6, 15)) is False  # день срока ещё годен
    assert codec.is_expired(p, date(2026, 6, 14)) is False


def test_expiry_far_future_within_uint16():
    far = codec._EPOCH + timedelta(days=65535)
    assert codec.verify(SECRET, _mint(key_expiry=far)).key_expiry == far
