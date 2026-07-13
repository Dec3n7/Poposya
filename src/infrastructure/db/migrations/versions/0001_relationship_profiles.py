"""relationship_profiles

Revision ID: 0001
Revises:
Create Date: 2026-07-08

"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "relationship_profiles",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("points_awarded_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_award_date", sa.Date(), nullable=True),
        sa.Column("is_exclusive", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("frozen_by_admin", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_dialog_at", sa.DateTime(), nullable=True),
        sa.Column("user_notes", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("user_id", "guild_id"),
    )
    op.create_index(
        "uq_relationship_exclusive_per_guild",
        "relationship_profiles",
        ["guild_id"],
        unique=True,
        sqlite_where=sa.text("is_exclusive = 1"),
        postgresql_where=sa.text("is_exclusive"),
    )
    op.create_index(
        "ix_relationship_guild_points",
        "relationship_profiles",
        ["guild_id", "points"],
    )


def downgrade() -> None:
    op.drop_index("ix_relationship_guild_points", table_name="relationship_profiles")
    op.drop_index("uq_relationship_exclusive_per_guild", table_name="relationship_profiles")
    op.drop_table("relationship_profiles")
