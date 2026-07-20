"""bot_profile.banner_data (загруженный баннер, base64 — кэш)

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-20

"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_profile",
        sa.Column("banner_data", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("bot_profile", "banner_data")
