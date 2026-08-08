"""ачивки сервера: открытые достижения участников

Revision ID: 0039
Revises: 0038
Create Date: 2026-08-08

Одна таблица `unlocked_achievements(user_id, guild_id, achievement_id, unlocked_at)`.
Каталог ачивок — в коде (domain/achievements/catalog.py), в БД хранится только
факт открытия. Составной PK даёт идемпотентность выдачи.
"""

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "unlocked_achievements",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), primary_key=True),
        sa.Column("achievement_id", sa.String(length=64), primary_key=True),
        sa.Column("unlocked_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_unlocked_ach_user", "unlocked_achievements", ["guild_id", "user_id"])


def downgrade() -> None:
    op.drop_index("ix_unlocked_ach_user", table_name="unlocked_achievements")
    op.drop_table("unlocked_achievements")
