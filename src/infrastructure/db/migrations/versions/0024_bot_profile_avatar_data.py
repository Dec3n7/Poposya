"""bot_profile.avatar_data (загруженный аватар, base64 — кэш)

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-19

"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_profile",
        sa.Column("avatar_data", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("bot_profile", "avatar_data")
