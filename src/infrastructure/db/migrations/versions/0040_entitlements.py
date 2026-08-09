"""тарифы серверов (подписки, выдаются оператором вручную)

Revision ID: 0040
Revises: 0039
Create Date: 2026-08-09

Одна таблица `guild_entitlements(guild_id, tier, expires_at, granted_by,
updated_at)` — одна строка на сервер. Отсутствие строки = тариф по умолчанию
(ENTITLEMENTS_DEFAULT_TIER). expires_at NULL = бессрочно.
"""

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guild_entitlements",
        sa.Column("guild_id", sa.BigInteger(), primary_key=True),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("granted_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("guild_entitlements")
