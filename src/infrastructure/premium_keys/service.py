"""Сервис лицензионных ключей Premium/Pro (docs/plans/premium-keys.md §3–§4).

Выпуск партий (пул по SKU), активация ключа (офлайн-verify → гейт отзыва →
rate-limit → сит-логика → атомарный grant), отзыв партии (soft/hard), инвентарь и
перевыпуск ключей для панели. Ключи самоподписаны (codec) — в БД пула валидных
ключей нет; полный ключ перевыпускается из реестра ЛИШЬ с секретом.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from secrets import randbits

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.interfaces.entitlements import PlanTier
from src.infrastructure.db.dml import rows_affected
from src.infrastructure.db.models.premium_keys import (
    KeySeatModel,
    PremiumKeyAttemptModel,
    PremiumKeyBatchModel,
    PremiumKeyIssuedModel,
)
from src.infrastructure.entitlements import EntitlementService, parse_tier
from src.infrastructure.premium_keys import codec

logger = logging.getLogger(__name__)

# верхний предел на размер партии — предохранитель от опечатки «count=100000»
_MAX_BATCH = 10_000


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _today() -> date:
    return datetime.now(UTC).date()


class RedeemOutcome(StrEnum):
    """Исход активации (пишется в журнал попыток; ≤16 символов)."""

    OK = "ok"  # сит занят, Premium выдан
    EXTENDED = "extended"  # тот же сервер — срок продлён, сит не тратится
    INVALID = "invalid"  # подпись/формат/несуществующая партия
    EXPIRED = "expired"  # ключ протух на полке
    REVOKED = "revoked"  # партия отозвана
    FULL = "full"  # все ситы ключа заняты
    RATE_LIMITED = "rate_limited"  # слишком много попыток


@dataclass(frozen=True)
class RedeemResult:
    outcome: RedeemOutcome
    message: str
    tier: PlanTier | None = None
    expires_at: datetime | None = None
    seats_used: int | None = None
    seats_total: int | None = None

    @property
    def ok(self) -> bool:
        return self.outcome in (RedeemOutcome.OK, RedeemOutcome.EXTENDED)


@dataclass(frozen=True)
class MintedBatch:
    batch_id: int
    label: str
    tier: PlanTier
    duration_days: int
    seats: int
    keys: list[str]


@dataclass(frozen=True)
class RevokeResult:
    batch_id: int
    hard: bool
    guilds_stripped: int


@dataclass(frozen=True)
class BatchView:
    batch_id: int
    label: str
    tier: PlanTier
    duration_days: int
    seats: int
    issued: int  # сколько ключей выпущено
    redeemed_seats: int  # сколько ситов потрачено
    capacity: int  # issued * seats — всего слотов в партии
    revoked: bool
    created_at: datetime
    note: str | None


@dataclass(frozen=True)
class KeyView:
    key: str  # перевыпущенный полный ключ (из реестра + секрет)
    nonce: str
    seats_used: int
    seats_total: int
    status: str  # unredeemed | partial | full


@dataclass(frozen=True)
class SkuView:
    tier: PlanTier
    duration_days: int
    issued: int
    redeemed_seats: int
    capacity: int
    remaining: int


@dataclass(frozen=True)
class ActivationView:
    """Успешная активация (потраченный сит): кто/где/когда/каким ключом (§7)."""

    nonce: str
    key_masked: str  # …хвост перевыпущенного ключа — корреляция без раскрытия
    guild_id: int
    user_id: int  # redeemed_by_user
    tier: PlanTier
    duration_days: int
    batch_id: int
    batch_label: str
    redeemed_at: datetime


@dataclass(frozen=True)
class AttemptView:
    """Попытка активации (успех и отказ) — видимость перебора/абуза (§4)."""

    user_id: int
    guild_id: int
    at: datetime
    outcome: str


class PremiumKeyService:
    """См. модуль. `enabled` False при пустом секрете — фича выключена."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        entitlements: EntitlementService,
        secret: str,
        *,
        attempts_per_hour: int = 2,
        shelf_life_days: int = 730,
    ):
        self._sf = session_factory
        self._ent = entitlements
        self._secret = secret
        self._attempts_per_hour = attempts_per_hour
        self._shelf_life_days = shelf_life_days

    @property
    def enabled(self) -> bool:
        return bool(self._secret)

    # ── выпуск партии (пул по SKU, §1a) ─────────────────────────────────────

    async def mint_batch(
        self,
        *,
        tier: PlanTier,
        duration_days: int,
        count: int,
        label: str,
        created_by: int,
        note: str | None = None,
    ) -> MintedBatch:
        """Выпускает партию из `count` самоподписанных ключей одного SKU. Пишет
        строку партии + реестр выпущенного (nonce↔batch); возвращает готовые ключи
        (только тут они существуют в открытом виде — дальше перевыпуск из реестра)."""
        if not self.enabled:
            raise RuntimeError("KEY_SIGNING_SECRET не задан — выпуск ключей выключен")
        if not 1 <= count <= _MAX_BATCH:
            raise ValueError(f"count вне 1..{_MAX_BATCH}")
        seats = codec.default_seats(tier)  # валидирует не-free тариф
        if duration_days not in codec.KEY_DURATIONS:
            raise ValueError(f"duration_days должен быть из {codec.KEY_DURATIONS}")
        key_expiry = _today() + timedelta(days=self._shelf_life_days)
        async with self._sf() as session:
            batch = PremiumKeyBatchModel(
                label=label,
                tier=tier.name.lower(),
                seats=seats,
                duration_days=duration_days,
                key_expiry=key_expiry,
                count=count,
                created_by=created_by,
                created_at=_now(),
                note=note,
            )
            session.add(batch)
            await session.flush()  # получить batch_id до генерации ключей
            batch_id = batch.batch_id
            keys: list[str] = []
            for _ in range(count):
                nonce = randbits(64)
                keys.append(
                    codec.mint(
                        self._secret,
                        tier=tier,
                        duration_days=duration_days,
                        key_expiry=key_expiry,
                        batch_id=batch_id,
                        seats=seats,
                        nonce=nonce,
                    )
                )
                session.add(PremiumKeyIssuedModel(nonce=f"{nonce:016x}", batch_id=batch_id))
            await session.commit()
        logger.info(
            "Выпущена партия %s (%s×%s, %s дн, %d ключей)",
            batch_id,
            tier.name.lower(),
            seats,
            duration_days,
            count,
        )
        return MintedBatch(batch_id, label, tier, duration_days, seats, keys)

    # ── активация ключа (§3–§4) ─────────────────────────────────────────────

    async def redeem(self, key: str, guild_id: int, user_id: int) -> RedeemResult:
        """Активирует ключ на сервере. Порядок (§4): rate-limit → офлайн-verify →
        expiry → (в транзакции с блокировкой партии) гейт отзыва → сит-логика →
        АТОМАРНО сит + grant. Все попытки (успех и отказ) идут в журнал."""
        if not self.enabled:
            return RedeemResult(RedeemOutcome.INVALID, "Активация ключей сейчас недоступна.")
        now = _now()

        # 1) rate-limit ДО касания ключа
        window_start = now - timedelta(hours=1)
        async with self._sf() as session:
            recent = (
                await session.execute(
                    select(func.count())
                    .select_from(PremiumKeyAttemptModel)
                    .where(
                        PremiumKeyAttemptModel.user_id == user_id,
                        PremiumKeyAttemptModel.at >= window_start,
                    )
                )
            ).scalar_one()
        if recent >= self._attempts_per_hour:
            await self._log(user_id, guild_id, RedeemOutcome.RATE_LIMITED, now)
            return RedeemResult(
                RedeemOutcome.RATE_LIMITED, "Слишком много попыток — попробуйте через час."
            )

        # 2) офлайн-проверка подписи и срока годности
        payload = codec.verify(self._secret, key)
        if payload is None:
            await self._log(user_id, guild_id, RedeemOutcome.INVALID, now)
            return RedeemResult(RedeemOutcome.INVALID, "Неверный ключ.")
        if codec.is_expired(payload, now.date()):
            await self._log(user_id, guild_id, RedeemOutcome.EXPIRED, now)
            return RedeemResult(RedeemOutcome.EXPIRED, "Ключ просрочен.")

        nonce_hex = payload.nonce_hex
        # 3) транзакция: блокируем партию (сериализация с отзывом, §3a), сит-логика
        async with self._sf() as session:
            batch = (
                await session.execute(
                    select(PremiumKeyBatchModel)
                    .where(PremiumKeyBatchModel.batch_id == payload.batch_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if batch is None:  # валидная подпись без партии — не бывает, но не падаем
                await self._log_in(session, user_id, guild_id, RedeemOutcome.INVALID, now)
                await session.commit()
                return RedeemResult(RedeemOutcome.INVALID, "Неверный ключ.")
            if batch.revoked_at is not None:
                await self._log_in(session, user_id, guild_id, RedeemOutcome.REVOKED, now)
                await session.commit()
                return RedeemResult(RedeemOutcome.REVOKED, "Ключ отозван.")

            seats = (
                (await session.execute(select(KeySeatModel).where(KeySeatModel.nonce == nonce_hex)))
                .scalars()
                .all()
            )
            taken = {s.guild_id for s in seats}
            extended = guild_id in taken
            if not extended and len(seats) >= payload.seats:
                await self._log_in(session, user_id, guild_id, RedeemOutcome.FULL, now)
                await session.commit()
                return RedeemResult(
                    RedeemOutcome.FULL,
                    f"Все {payload.seats} слот(ов) этого ключа уже заняты.",
                    seats_used=len(seats),
                    seats_total=payload.seats,
                )

            # продление: срок стакается от текущего активного (§6), иначе от now
            _, cur_expires, active = self._ent.current(guild_id)
            base = cur_expires if (active and cur_expires is not None) else now
            new_expires = max(now, base) + timedelta(days=payload.duration_days)

            if not extended:
                session.add(
                    KeySeatModel(
                        nonce=nonce_hex,
                        guild_id=guild_id,
                        tier=payload.tier.name.lower(),
                        redeemed_by_user=user_id,
                        redeemed_at=now,
                    )
                )
            # grant в ТОЙ ЖЕ транзакции — сит и Premium атомарны (§3, шаг 4)
            await self._ent.upsert_in_session(session, guild_id, payload.tier, new_expires, user_id)
            outcome = RedeemOutcome.EXTENDED if extended else RedeemOutcome.OK
            await self._log_in(session, user_id, guild_id, outcome, now)
            await session.commit()

        await self._ent.reload_guild(guild_id)  # обновить кэш тарифов после commit
        used = len(taken) if extended else len(taken) + 1
        msg = "Срок Premium продлён." if extended else f"{payload.tier.name.title()} активирован."
        return RedeemResult(
            outcome,
            msg,
            tier=payload.tier,
            expires_at=new_expires,
            seats_used=used,
            seats_total=payload.seats,
        )

    # ── отзыв партии (§3a) ──────────────────────────────────────────────────

    async def revoke_batch(
        self, batch_id: int, *, revoked_by: int, reason: str, hard: bool = False
    ) -> RevokeResult:
        """Отзыв партии. soft: помечает партию — будущие активации её ключей
        отклоняются. hard: плюс снимает уже выданное (по всем серверам партии
        `EntitlementService.revoke`). Идемпотентно: повторный отзыв — no-op по мете."""
        affected: list[int] = []
        async with self._sf() as session:
            batch = (
                await session.execute(
                    select(PremiumKeyBatchModel)
                    .where(PremiumKeyBatchModel.batch_id == batch_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if batch is None:
                raise ValueError(f"партии {batch_id} нет")
            if batch.revoked_at is None:  # не затираем исходную мету при повторе
                batch.revoked_at = _now()
                batch.revoked_by = revoked_by
                batch.revoke_reason = reason
            if hard:
                affected = list(
                    (
                        await session.execute(
                            select(KeySeatModel.guild_id)
                            .join(
                                PremiumKeyIssuedModel,
                                KeySeatModel.nonce == PremiumKeyIssuedModel.nonce,
                            )
                            .where(PremiumKeyIssuedModel.batch_id == batch_id)
                            .distinct()
                        )
                    )
                    .scalars()
                    .all()
                )
            await session.commit()

        stripped = 0
        if hard:
            for gid in affected:
                if await self._ent.revoke(gid):
                    stripped += 1
        logger.warning(
            "Партия %s отозвана (%s, задето серверов: %d)",
            batch_id,
            "hard" if hard else "soft",
            stripped,
        )
        return RevokeResult(batch_id, hard, stripped)

    async def reactivate_batch(self, batch_id: int) -> bool:
        """Снять soft-отзыв партии (ключи снова активируются). Hard-гранты назад НЕ
        возвращает. True — партия была отозвана."""
        async with self._sf() as session:
            batch = (
                await session.execute(
                    select(PremiumKeyBatchModel)
                    .where(PremiumKeyBatchModel.batch_id == batch_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if batch is None:
                raise ValueError(f"партии {batch_id} нет")
            was = batch.revoked_at is not None
            batch.revoked_at = None
            batch.revoked_by = None
            batch.revoke_reason = None
            await session.commit()
        return was

    # ── инвентарь и перевыпуск для панели ───────────────────────────────────

    def _remint(self, batch: PremiumKeyBatchModel, nonce_hex: str) -> str:
        """Перевыпуск точного ключа из реестра + секрет (детерминизм codec)."""
        return codec.mint(
            self._secret,
            tier=parse_tier(batch.tier),
            duration_days=batch.duration_days,
            key_expiry=batch.key_expiry,
            batch_id=batch.batch_id,
            seats=batch.seats,
            nonce=int(nonce_hex, 16),
        )

    async def list_batches(self) -> list[BatchView]:
        """Список партий с агрегатами (для вкладки пула). Новые сверху."""
        async with self._sf() as session:
            batches = list(
                (
                    await session.execute(
                        select(PremiumKeyBatchModel).order_by(PremiumKeyBatchModel.batch_id.desc())
                    )
                )
                .scalars()
                .all()
            )
            # выпущено ключей на партию
            issued_counts: dict[int, int] = {
                bid: int(cnt)
                for bid, cnt in (
                    await session.execute(
                        select(PremiumKeyIssuedModel.batch_id, func.count()).group_by(
                            PremiumKeyIssuedModel.batch_id
                        )
                    )
                ).all()
            }
            # потрачено ситов на партию (join issued↔seats)
            redeemed_counts: dict[int, int] = {
                bid: int(cnt)
                for bid, cnt in (
                    await session.execute(
                        select(PremiumKeyIssuedModel.batch_id, func.count())
                        .join(KeySeatModel, KeySeatModel.nonce == PremiumKeyIssuedModel.nonce)
                        .group_by(PremiumKeyIssuedModel.batch_id)
                    )
                ).all()
            }
        views: list[BatchView] = []
        for b in batches:
            issued = int(issued_counts.get(b.batch_id, 0))
            redeemed = int(redeemed_counts.get(b.batch_id, 0))
            views.append(
                BatchView(
                    batch_id=b.batch_id,
                    label=b.label,
                    tier=parse_tier(b.tier),
                    duration_days=b.duration_days,
                    seats=b.seats,
                    issued=issued,
                    redeemed_seats=redeemed,
                    capacity=issued * b.seats,
                    revoked=b.revoked_at is not None,
                    created_at=b.created_at,
                    note=b.note,
                )
            )
        return views

    async def batch_keys(self, batch_id: int) -> list[KeyView]:
        """Ключи партии с перевыпуском (панель показывает САМИ ключи) и статусом
        по потраченным ситам. Требует секрет — без него ключей не воссоздать."""
        if not self.enabled:
            raise RuntimeError("KEY_SIGNING_SECRET не задан — ключи не перевыпустить")
        async with self._sf() as session:
            batch = await session.get(PremiumKeyBatchModel, batch_id)
            if batch is None:
                raise ValueError(f"партии {batch_id} нет")
            nonces = list(
                (
                    await session.execute(
                        select(PremiumKeyIssuedModel.nonce).where(
                            PremiumKeyIssuedModel.batch_id == batch_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            seat_counts: dict[str, int] = {}
            if nonces:
                seat_counts = {
                    nonce: int(cnt)
                    for nonce, cnt in (
                        await session.execute(
                            select(KeySeatModel.nonce, func.count())
                            .where(KeySeatModel.nonce.in_(nonces))
                            .group_by(KeySeatModel.nonce)
                        )
                    ).all()
                }
        views: list[KeyView] = []
        for nonce in nonces:
            used = int(seat_counts.get(nonce, 0))
            status = "unredeemed" if used == 0 else ("full" if used >= batch.seats else "partial")
            views.append(
                KeyView(
                    key=self._remint(batch, nonce),
                    nonce=nonce,
                    seats_used=used,
                    seats_total=batch.seats,
                    status=status,
                )
            )
        return views

    async def export_batch(self, batch_id: int, *, only_unredeemed: bool = False) -> list[str]:
        """Плоский список ключей партии для выгрузки (Export CSV / файл на Boosty).
        only_unredeemed — лишь ещё не тронутые (для дозаливки в пул)."""
        keys = await self.batch_keys(batch_id)
        if only_unredeemed:
            keys = [k for k in keys if k.status == "unredeemed"]
        return [k.key for k in keys]

    async def sku_inventory(self) -> list[SkuView]:
        """Пул по SKU (tier × duration): выпущено/потрачено/остаток. Отозванные
        партии в остаток не идут."""
        batches = await self.list_batches()
        agg: dict[tuple[PlanTier, int], list[int]] = {}
        for b in batches:
            key = (b.tier, b.duration_days)
            issued, redeemed, remaining = agg.setdefault(key, [0, 0, 0])
            agg[key][0] = issued + b.issued
            agg[key][1] = redeemed + b.redeemed_seats
            # остаток слотов активных партий
            agg[key][2] = remaining + (0 if b.revoked else b.capacity - b.redeemed_seats)
        return [
            SkuView(
                tier=tier,
                duration_days=dur,
                issued=vals[0],
                redeemed_seats=vals[1],
                capacity=sum(
                    bb.capacity for bb in batches if bb.tier == tier and bb.duration_days == dur
                ),
                remaining=vals[2],
            )
            for (tier, dur), vals in sorted(agg.items(), key=lambda kv: (kv[0][0].value, kv[0][1]))
        ]

    # ── журнал активаций и освобождение сита (§3, §7) ──────────────────────

    async def list_activations(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        guild_id: int | None = None,
        user_id: int | None = None,
    ) -> list[ActivationView]:
        """Успешные активации (потраченные ситы) — кто/где/когда/каким ключом.
        Новые сверху. Фильтры по серверу и пользователю. Ключ маскируется
        (перевыпуск + хвост), полностью в журнал не светим (§7)."""
        stmt = (
            select(KeySeatModel, PremiumKeyBatchModel)
            .join(PremiumKeyIssuedModel, KeySeatModel.nonce == PremiumKeyIssuedModel.nonce)
            .join(
                PremiumKeyBatchModel,
                PremiumKeyIssuedModel.batch_id == PremiumKeyBatchModel.batch_id,
            )
        )
        if guild_id is not None:
            stmt = stmt.where(KeySeatModel.guild_id == guild_id)
        if user_id is not None:
            stmt = stmt.where(KeySeatModel.redeemed_by_user == user_id)
        stmt = stmt.order_by(KeySeatModel.redeemed_at.desc()).limit(limit).offset(offset)
        async with self._sf() as session:
            rows = list((await session.execute(stmt)).all())
        views: list[ActivationView] = []
        for seat, batch in rows:
            masked = (
                "…" + self._remint(batch, seat.nonce)[-6:]
                if self.enabled
                else "…" + seat.nonce[-6:]
            )
            views.append(
                ActivationView(
                    nonce=seat.nonce,
                    key_masked=masked,
                    guild_id=seat.guild_id,
                    user_id=seat.redeemed_by_user,
                    tier=parse_tier(seat.tier),
                    duration_days=batch.duration_days,
                    batch_id=batch.batch_id,
                    batch_label=batch.label,
                    redeemed_at=seat.redeemed_at,
                )
            )
        return views

    async def list_attempts(self, *, limit: int = 100, offset: int = 0) -> list[AttemptView]:
        """Лента попыток активации (успех и отказ) — видимость перебора. Ключ тут
        не хранится (журнал попыток лёгкий), только пользователь/сервер/исход."""
        async with self._sf() as session:
            rows = list(
                (
                    await session.execute(
                        select(PremiumKeyAttemptModel)
                        .order_by(PremiumKeyAttemptModel.at.desc())
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
        return [AttemptView(a.user_id, a.guild_id, a.at, a.outcome) for a in rows]

    async def release_seat(self, nonce: str, guild_id: int) -> bool:
        """Точечно снять сервер с лицензии (§3): удалить сит (nonce, guild) и снять
        Premium с этого сервера. Сит возвращается — ключ можно активировать на
        другом сервере. True — сит был и освобождён; False — такого сита нет."""
        async with self._sf() as session:
            res = await session.execute(
                delete(KeySeatModel).where(
                    KeySeatModel.nonce == nonce, KeySeatModel.guild_id == guild_id
                )
            )
            existed = rows_affected(res) > 0
            await session.commit()
        if existed:
            await self._ent.revoke(guild_id)  # снять Premium с сервера
            logger.info("Сит (%s…, guild %s) освобождён, Premium снят", nonce[:6], guild_id)
        return existed

    # ── журнал попыток ──────────────────────────────────────────────────────

    async def _log(self, user_id: int, guild_id: int, outcome: RedeemOutcome, at: datetime) -> None:
        async with self._sf() as session:
            await self._log_in(session, user_id, guild_id, outcome, at)
            await session.commit()

    @staticmethod
    async def _log_in(
        session: AsyncSession, user_id: int, guild_id: int, outcome: RedeemOutcome, at: datetime
    ) -> None:
        session.add(
            PremiumKeyAttemptModel(user_id=user_id, guild_id=guild_id, at=at, outcome=outcome.value)
        )
