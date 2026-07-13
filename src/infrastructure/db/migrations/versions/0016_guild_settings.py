"""настройки на уровне сервера (переопределение .env через /config)

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-12

"""

from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guild_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.UniqueConstraint("guild_id", "key", name="uq_guild_setting"),
    )


def downgrade() -> None:
    op.drop_table("guild_settings")
