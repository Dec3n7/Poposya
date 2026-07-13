"""ночные находки: находки, коллекции, попытки

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-10

"""

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "night_finds",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("location_id", sa.Text(), nullable=False),
        sa.Column("item_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("message_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("claimed_by", sa.BigInteger(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_night_finds_guild_active",
        "night_finds",
        ["guild_id", "claimed_by", "expires_at"],
    )
    op.create_index("ix_night_finds_message", "night_finds", ["message_id"])

    op.create_table(
        "user_collections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("item_id", sa.Text(), nullable=False),
        sa.Column("obtained_at", sa.DateTime(), nullable=False),
        sa.Column("gifted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_user_collections_owner",
        "user_collections",
        ["guild_id", "user_id", "obtained_at"],
    )

    op.create_table(
        "find_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
        sa.Column("find_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_find_attempts_user",
        "find_attempts",
        ["guild_id", "user_id", "kind", "attempted_at"],
    )
    op.create_index("ix_find_attempts_find", "find_attempts", ["find_id", "user_id"])


def downgrade() -> None:
    op.drop_index("ix_find_attempts_find", table_name="find_attempts")
    op.drop_index("ix_find_attempts_user", table_name="find_attempts")
    op.drop_table("find_attempts")
    op.drop_index("ix_user_collections_owner", table_name="user_collections")
    op.drop_table("user_collections")
    op.drop_index("ix_night_finds_message", table_name="night_finds")
    op.drop_index("ix_night_finds_guild_active", table_name="night_finds")
    op.drop_table("night_finds")
