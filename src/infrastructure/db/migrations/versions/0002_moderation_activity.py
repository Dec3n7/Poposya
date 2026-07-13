"""warns + member_activity

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-08

"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "warns",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("moderator_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_warns_guild_user", "warns", ["guild_id", "user_id"])

    op.create_table(
        "member_activity",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "guild_id"),
    )


def downgrade() -> None:
    op.drop_table("member_activity")
    op.drop_index("ix_warns_guild_user", table_name="warns")
    op.drop_table("warns")
