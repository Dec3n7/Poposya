"""message_activity (почасовой счётчик сообщений — хитмап и сообщения/день)

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-20

"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_activity",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("bucket_date", sa.Date(), nullable=False),
        sa.Column("bucket_hour", sa.SmallInteger(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("guild_id", "bucket_date", "bucket_hour"),
    )


def downgrade() -> None:
    op.drop_table("message_activity")
