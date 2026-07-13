"""киноклуб: вотчлист, киновечера, оценки

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-10

"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cinema_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("added_by", sa.BigInteger(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("overview", sa.Text(), nullable=False, server_default=""),
        sa.Column("poster_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("message_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("channel_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default="listed"),
        sa.Column("rating_message_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rating_ends_at", sa.DateTime(), nullable=True),
        sa.Column("avg_score", sa.Float(), nullable=True),
        sa.Column("ratings_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("poposya_score", sa.Integer(), nullable=True),
        sa.Column("poposya_review", sa.Text(), nullable=False, server_default=""),
        sa.Column("watched_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cinema_entries_guild_status", "cinema_entries", ["guild_id", "status"])
    op.create_index("ix_cinema_entries_message", "cinema_entries", ["message_id"])
    op.create_index("ix_cinema_entries_rating_message", "cinema_entries", ["rating_message_id"])

    op.create_table(
        "cinema_votes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_id", "user_id", name="uq_cinema_vote"),
    )

    op.create_table(
        "cinema_nights",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False),
        sa.Column("poll_ends_at", sa.DateTime(), nullable=False),
        sa.Column("candidates_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.Text(), nullable=False, server_default="poll"),
        sa.Column("channel_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("poll_message_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("winner_message_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("winner_entry_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cinema_nights_guild_status", "cinema_nights", ["guild_id", "status"])

    op.create_table(
        "cinema_night_votes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("night_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("night_id", "user_id", name="uq_cinema_night_vote"),
    )

    op.create_table(
        "cinema_ratings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("rated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_id", "user_id", name="uq_cinema_rating"),
    )


def downgrade() -> None:
    op.drop_table("cinema_ratings")
    op.drop_table("cinema_night_votes")
    op.drop_index("ix_cinema_nights_guild_status", table_name="cinema_nights")
    op.drop_table("cinema_nights")
    op.drop_table("cinema_votes")
    op.drop_index("ix_cinema_entries_rating_message", table_name="cinema_entries")
    op.drop_index("ix_cinema_entries_message", table_name="cinema_entries")
    op.drop_index("ix_cinema_entries_guild_status", table_name="cinema_entries")
    op.drop_table("cinema_entries")
