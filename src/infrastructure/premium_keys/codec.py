"""Кодек лицензионных ключей Premium/Pro: самоподписанный, проверяемый ОФЛАЙН.

Формат (docs/plans/premium-keys.md §1):

    POPO-<base32(payload)>-<base32(sig)>
    payload = version · tier · seats · duration_days · key_expiry · batch_id · nonce
    sig     = HMAC-SHA256(SECRET, payload)[:12 байт]   (~96 бит — не подделать)

Проверка не ходит ни в БД, ни в сеть: ключ сам доказывает подлинность и несёт
tier/срок/ситы/партию ПОД подписью — подменить нельзя, не зная секрет. Секрет
живёт только в окружении. base32 (алфавит A–Z2–7) — без 0/1/8/9 и регистра, чтобы
ключ было не спутать при перепечатке.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import struct
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
from secrets import randbits

from src.application.interfaces.entitlements import PlanTier

PREFIX = "POPO"
_VERSION = 1
# усечение HMAC до 12 байт = 96 бит: перебрать подпись нереально, а ключ короче.
_SIG_BYTES = 12
# эпоха отсчёта key_expiry: дни от неё влезают в uint16 (~179 лет) и начинаются с
# малых чисел. Менять НЕЛЬЗЯ — сдвиг эпохи ломает проверку уже выпущенных ключей.
_EPOCH = date(2026, 1, 1)
# version(B) tier(B) seats(B) duration(H) key_expiry_days(H) batch_id(I) nonce(Q)
_STRUCT = ">BBBHHIQ"
_PAYLOAD_BYTES = struct.calcsize(_STRUCT)  # 19

# допустимые длительности-SKU (план §1a). mint вне множества запрещён.
KEY_DURATIONS: tuple[int, ...] = (30, 90, 180, 365)
# ситы по тарифу (план §3): premium — 1 сервер, pro — 5.
SEATS_BY_TIER: dict[PlanTier, int] = {PlanTier.PREMIUM: 1, PlanTier.PRO: 5}

_UINT16_MAX = 0xFFFF
_UINT32_MAX = 0xFFFFFFFF
_UINT64_MAX = 0xFFFFFFFFFFFFFFFF


@dataclass(frozen=True)
class KeyPayload:
    """Расшифрованная (и уже проверенная подписью) начинка ключа."""

    tier: PlanTier
    seats: int
    duration_days: int
    key_expiry: date  # срок годности САМОГО ключа на полке (не срок Premium)
    batch_id: int
    nonce: int

    @property
    def nonce_hex(self) -> str:
        """Стабильное строковое представление nonce для БД (`key_seats.nonce`)."""
        return f"{self.nonce:016x}"


def _b32(raw: bytes) -> str:
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _unb32(s: str) -> bytes:
    return base64.b32decode(s + "=" * (-len(s) % 8))  # добираем padding; мусор → Error


def _sign(secret: str, payload: bytes) -> bytes:
    return hmac.new(secret.encode(), payload, sha256).digest()[:_SIG_BYTES]


def default_seats(tier: PlanTier) -> int:
    """Ситы тарифа (premium=1, pro=5). Ошибка для непродаваемого free."""
    if tier not in SEATS_BY_TIER:
        raise ValueError(f"нельзя выпускать ключ тарифа {tier.name}")
    return SEATS_BY_TIER[tier]


def mint(
    secret: str,
    *,
    tier: PlanTier,
    duration_days: int,
    key_expiry: date,
    batch_id: int,
    seats: int | None = None,
    nonce: int | None = None,
) -> str:
    """Собирает один самоподписанный ключ. `seats` по умолчанию из тарифа; `nonce`
    по умолчанию — свежие 64 бита. Валидирует SKU-длительность, тариф и границы.

    Детерминирован по (secret + все поля): один и тот же payload с тем же nonce
    даёт тот же ключ — на этом стоит перевыпуск из реестра (план §2)."""
    seats = default_seats(tier) if seats is None else seats
    if duration_days not in KEY_DURATIONS:
        raise ValueError(f"duration_days должен быть из {KEY_DURATIONS}, дано {duration_days}")
    if not 1 <= seats <= 255:
        raise ValueError("seats вне 1..255")
    if not 0 <= batch_id <= _UINT32_MAX:
        raise ValueError("batch_id вне uint32")
    expiry_days = (key_expiry - _EPOCH).days
    if not 0 <= expiry_days <= _UINT16_MAX:
        raise ValueError("key_expiry вне диапазона (эпоха .. +65535 дней)")
    nonce = randbits(64) if nonce is None else nonce
    if not 0 <= nonce <= _UINT64_MAX:
        raise ValueError("nonce вне uint64")
    payload = struct.pack(
        _STRUCT, _VERSION, int(tier), seats, duration_days, expiry_days, batch_id, nonce
    )
    return f"{PREFIX}-{_b32(payload)}-{_b32(_sign(secret, payload))}"


def verify(secret: str, key: str) -> KeyPayload | None:
    """Разбирает и проверяет подпись ОФЛАЙН. Возвращает payload либо None при любой
    неудаче — единая ошибка, чтобы не подсказывать перебору, где именно не сошлось.

    `key_expiry` тут НЕ проверяется: срок годности ключа — отдельный исход, его
    решает вызывающий (редемпшн: просрочен → «неверный ключ», но отдельная метка в
    журнале). Сравнение подписи — `hmac.compare_digest` (константное время)."""
    parts = key.strip().upper().split("-")
    if len(parts) != 3 or parts[0] != PREFIX:
        return None
    try:
        payload = _unb32(parts[1])
        sig = _unb32(parts[2])
    except (binascii.Error, ValueError):  # кривой base32 — единый None
        return None
    if len(payload) != _PAYLOAD_BYTES or len(sig) != _SIG_BYTES:
        return None
    if not hmac.compare_digest(sig, _sign(secret, payload)):
        return None
    version, tier_code, seats, duration, expiry_days, batch_id, nonce = struct.unpack(
        _STRUCT, payload
    )
    if version != _VERSION:
        return None
    try:
        tier = PlanTier(tier_code)
    except ValueError:
        return None
    if tier not in SEATS_BY_TIER:  # free под ключом быть не может
        return None
    return KeyPayload(
        tier=tier,
        seats=seats,
        duration_days=duration,
        key_expiry=_EPOCH + timedelta(days=expiry_days),
        batch_id=batch_id,
        nonce=nonce,
    )


def is_expired(payload: KeyPayload, today: date) -> bool:
    """Ключ протух на полке (не выкупили вовремя). Срок Premium ПОСЛЕ активации —
    это `duration_days`, здесь ни при чём."""
    return payload.key_expiry < today
