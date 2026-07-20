"""bot_profile (пер-серверный профиль бота: ник/аватар/баннер)

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-19

"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_profile",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("nick", sa.Text(), nullable=False),
        sa.Column("avatar_url", sa.Text(), nullable=False),
        sa.Column("banner_url", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("guild_id"),
    )


def downgrade() -> None:
    op.drop_table("bot_profile")
