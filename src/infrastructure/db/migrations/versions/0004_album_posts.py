"""album_posts

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-09

"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "album_posts",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("posted_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("guild_id", "message_id"),
    )


def downgrade() -> None:
    op.drop_table("album_posts")
