"""pending_kicks (фича «остаться или уйти»)

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-16

"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_kicks",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("remind_at", sa.DateTime(), nullable=False),
        sa.Column("kick_at", sa.DateTime(), nullable=False),
        sa.Column("reminded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("guild_id", "user_id"),
    )
    op.create_index("ix_pending_kicks_kick_at", "pending_kicks", ["kick_at"])
    op.create_index("ix_pending_kicks_remind_at", "pending_kicks", ["remind_at"])


def downgrade() -> None:
    op.drop_index("ix_pending_kicks_remind_at", table_name="pending_kicks")
    op.drop_index("ix_pending_kicks_kick_at", table_name="pending_kicks")
    op.drop_table("pending_kicks")
