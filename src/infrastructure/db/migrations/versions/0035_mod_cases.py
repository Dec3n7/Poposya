"""единый журнал действий модерации (mod_cases)

Revision ID: 0035
Revises: 0034
Create Date: 2026-07-31

Одна append-only таблица: и бот (слеш-команды), и панель (командный мост) пишут
сюда каждое действие модерации — варн, мут, кик, бан, чистка, антиспам. Даёт
полную историю по участнику (/history и карточка в панели) и питает лестницу
эскалации варнов (счёт прошлых авто-наказаний). Редактирования/удаления нет.
"""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mod_cases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("moderator_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False, server_default="bot"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_mod_cases_guild_user", "mod_cases", ["guild_id", "user_id"])


def downgrade() -> None:
    op.drop_index("ix_mod_cases_guild_user", table_name="mod_cases")
    op.drop_table("mod_cases")
