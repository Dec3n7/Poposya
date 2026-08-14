"""Лицензионные ключи Premium/Pro (docs/plans/premium-keys.md §2).

Четыре таблицы. Ключи самоподписаны (codec, §1) — в БД НЕ лежит пул валидных
ключей: только партии, реестр выпущенного (payload без подписи/секрета),
потраченные ситы и журнал попыток. Полный ключ перевыпускается из (партия, nonce)
лишь с env-секретом.
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class PremiumKeyBatchModel(Base):
    """Партия — единица пула (§1a) и ОТЗЫВА (§3a). Общие для всех ключей партии поля
    (tier/seats/duration/expiry) живут здесь, а не дублируются в каждом ключе.
    `batch_id` идёт в payload ключа под подписью — по нему редемпшн сверяет
    `revoked_at` (NULL = партия активна)."""

    __tablename__ = "premium_key_batches"

    batch_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)  # «boosty-2026q3»
    tier: Mapped[str] = mapped_column(String(16), nullable=False)  # premium | pro
    seats: Mapped[int] = mapped_column(Integer, nullable=False)  # premium=1, pro=5
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)  # 30/90/180/365
    key_expiry: Mapped[date] = mapped_column(Date, nullable=False)  # срок ключа на полке
    count: Mapped[int] = mapped_column(Integer, nullable=False)  # сколько выпущено
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)  # оператор
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # NULL = активна
    revoked_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)


class PremiumKeyIssuedModel(Base):
    """Реестр выпущенных ключей (вариант B): на ключ — только `nonce` + партия.
    tier/срок/ситы берутся из партии. Хранится лишь payload, ни подписи, ни
    секрета: полный ключ перевыпускается из (партия, nonce) с env-секретом."""

    __tablename__ = "premium_keys_issued"

    nonce: Mapped[str] = mapped_column(String(16), primary_key=True)  # hex 64-бит
    batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("premium_key_batches.batch_id"), nullable=False, index=True
    )


class KeySeatModel(Base):
    """Потраченный сит: (ключ `nonce`, сервер). Однократность-на-сервер и мультисит
    (§3). Композитный PK (nonce, guild_id) не даёт занять один сервер дважды тем же
    ключом. Единственное «что уже потрачено» — пула валидных ключей нет."""

    __tablename__ = "key_seats"

    nonce: Mapped[str] = mapped_column(String(16), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)  # зафиксированный tier ключа
    redeemed_by_user: Mapped[int] = mapped_column(BigInteger, nullable=False)  # кто активировал
    redeemed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class PremiumKeyAttemptModel(Base):
    """Журнал попыток активации (§4): rate-limit по (user_id, at) и видимость
    перебора. Пишется и на успех, и на отказ — иначе брутфорс обходил бы лимит.
    `outcome` ∈ ok|invalid|used|full|rate_limited|revoked|expired."""

    __tablename__ = "premium_key_attempts"
    __table_args__ = (Index("ix_premium_key_attempts_user_at", "user_id", "at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
