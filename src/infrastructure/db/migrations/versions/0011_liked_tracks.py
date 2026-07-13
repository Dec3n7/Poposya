"""лайкнутые треки пользователей

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-10

"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_liked_tracks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("uploader", sa.Text(), nullable=True),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("liked_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "video_id", name="uq_liked_user_video"),
    )
    op.create_index("ix_liked_user_time", "user_liked_tracks", ["user_id", "liked_at"])


def downgrade() -> None:
    op.drop_index("ix_liked_user_time", table_name="user_liked_tracks")
    op.drop_table("user_liked_tracks")
