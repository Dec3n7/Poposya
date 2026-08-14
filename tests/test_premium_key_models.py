"""Модели лицензионных ключей: роундтрип и ключевые ограничения схемы.

Импорт моделей здесь регистрирует их на `Base.metadata`, поэтому фикстура
`session_factory` (create_all) поднимает и эти таблицы.
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.infrastructure.db.models.premium_keys import (
    KeySeatModel,
    PremiumKeyAttemptModel,
    PremiumKeyBatchModel,
    PremiumKeyIssuedModel,
)

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC).replace(tzinfo=None)


async def _make_batch(session, **kw) -> int:
    batch = PremiumKeyBatchModel(
        label=kw.get("label", "boosty-test"),
        tier=kw.get("tier", "premium"),
        seats=kw.get("seats", 1),
        duration_days=kw.get("duration_days", 30),
        key_expiry=kw.get("key_expiry", date(2027, 1, 1)),
        count=kw.get("count", 10),
        created_by=kw.get("created_by", 1),
        created_at=NOW,
    )
    session.add(batch)
    await session.flush()
    return batch.batch_id


async def test_batch_autoincrements_and_defaults_active(session_factory):
    async with session_factory() as session:
        bid = await _make_batch(session)
        await session.commit()
    async with session_factory() as session:
        row = await session.get(PremiumKeyBatchModel, bid)
        assert row.batch_id == bid
        assert row.revoked_at is None  # партия активна по умолчанию
        assert row.revoke_reason is None


async def test_issued_key_links_batch(session_factory):
    async with session_factory() as session:
        bid = await _make_batch(session)
        session.add(PremiumKeyIssuedModel(nonce="abcdef0123456789", batch_id=bid))
        await session.commit()
    async with session_factory() as session:
        row = await session.get(PremiumKeyIssuedModel, "abcdef0123456789")
        assert row.batch_id == bid


async def test_key_seat_composite_pk_blocks_double_seat(session_factory):
    # один сервер нельзя занять дважды тем же ключом (nonce, guild_id) — PK
    async with session_factory() as session:
        session.add(
            KeySeatModel(
                nonce="n1", guild_id=100, tier="premium", redeemed_by_user=5, redeemed_at=NOW
            )
        )
        await session.commit()
    async with session_factory() as session:
        session.add(
            KeySeatModel(
                nonce="n1", guild_id=100, tier="premium", redeemed_by_user=9, redeemed_at=NOW
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_same_key_different_guilds_allowed(session_factory):
    # мультисит: один ключ на разных серверах — разные строки, ок
    async with session_factory() as session:
        session.add_all(
            [
                KeySeatModel(
                    nonce="n2", guild_id=1, tier="pro", redeemed_by_user=5, redeemed_at=NOW
                ),
                KeySeatModel(
                    nonce="n2", guild_id=2, tier="pro", redeemed_by_user=5, redeemed_at=NOW
                ),
            ]
        )
        await session.commit()
        seats = (
            (await session.execute(select(KeySeatModel).where(KeySeatModel.nonce == "n2")))
            .scalars()
            .all()
        )
        assert len(seats) == 2


async def test_attempt_log_roundtrip(session_factory):
    async with session_factory() as session:
        session.add(PremiumKeyAttemptModel(user_id=7, guild_id=100, at=NOW, outcome="rate_limited"))
        await session.commit()
        row = (await session.execute(select(PremiumKeyAttemptModel))).scalar_one()
        assert row.outcome == "rate_limited" and row.user_id == 7
