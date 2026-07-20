"""guild_metrics_daily (суточные снапшоты метрик — тренды на панели)

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-19

"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guild_metrics_daily",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("guild_id", "day", "metric"),
    )


def downgrade() -> None:
    op.drop_table("guild_metrics_daily")
