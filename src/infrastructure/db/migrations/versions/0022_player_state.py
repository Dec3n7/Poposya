"""player_state (снапшот живого плеера — live на панели)

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-19

"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_state",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("current", sa.Text(), nullable=True),
        sa.Column("queue", sa.Text(), nullable=False),
        sa.Column("position_seconds", sa.Integer(), nullable=False),
        sa.Column("position_at", sa.DateTime(), nullable=True),
        sa.Column("is_paused", sa.Boolean(), nullable=False),
        sa.Column("repeat", sa.Text(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("guild_id"),
    )


def downgrade() -> None:
    op.drop_table("player_state")
