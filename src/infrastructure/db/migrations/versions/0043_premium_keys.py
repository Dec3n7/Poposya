"""premium keys: партии, реестр выпущенного, потраченные ситы, журнал попыток

Revision ID: 0043
Revises: 0042
Create Date: 2026-08-14

Лицензионные ключи Premium/Pro (docs/plans/premium-keys.md). Ключи самоподписаны
(HMAC, офлайн-проверка) — в БД НЕ пул валидных ключей, а: партии (единица пула и
отзыва), реестр выпущенного (payload без подписи/секрета), потраченные ситы
(однократность-на-сервер + мультисит) и журнал попыток (rate-limit).
"""

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # партия — единица пула (§1a) и отзыва (§3a)
    op.create_table(
        "premium_key_batches",
        sa.Column("batch_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("key_expiry", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("note", sa.String(length=256), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by", sa.BigInteger(), nullable=True),
        sa.Column("revoke_reason", sa.String(length=256), nullable=True),
    )

    # реестр выпущенных: nonce + партия (payload, вариант B)
    op.create_table(
        "premium_keys_issued",
        sa.Column("nonce", sa.String(length=16), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("premium_key_batches.batch_id"),
            nullable=False,
        ),
    )
    op.create_index("ix_premium_keys_issued_batch_id", "premium_keys_issued", ["batch_id"])

    # потраченные ситы: (nonce, guild) — композитный PK против двойного занятия
    op.create_table(
        "key_seats",
        sa.Column("nonce", sa.String(length=16), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), primary_key=True),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("redeemed_by_user", sa.BigInteger(), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(), nullable=False),
    )

    # журнал попыток: rate-limit + видимость перебора (§4)
    op.create_table(
        "premium_key_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
    )
    op.create_index("ix_premium_key_attempts_user_at", "premium_key_attempts", ["user_id", "at"])


def downgrade() -> None:
    op.drop_index("ix_premium_key_attempts_user_at", table_name="premium_key_attempts")
    op.drop_table("premium_key_attempts")
    op.drop_table("key_seats")
    op.drop_index("ix_premium_keys_issued_batch_id", table_name="premium_keys_issued")
    op.drop_table("premium_keys_issued")
    op.drop_table("premium_key_batches")
