"""guild_roles + guild_role_meta (зеркало ролей сервера для веб-панели)

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-20

Бот держит актуальное состояние ролей в БД (бэкфилл на старте + gateway-события),
чтобы панель читала роли и иерархию без обращения к Discord. guild_role_meta —
одна строка на сервер: позиция высшей роли бота (граница «что боту доступно») и
момент последней синхронизации.
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guild_roles",
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("color", sa.Integer(), nullable=False),
        sa.Column("hoist", sa.Boolean(), nullable=False),
        sa.Column("mentionable", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("managed", sa.Boolean(), nullable=False),
        sa.Column("permissions", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("role_id"),
    )
    op.create_index("ix_guild_roles_guild_id", "guild_roles", ["guild_id"])

    op.create_table(
        "guild_role_meta",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("bot_user_id", sa.BigInteger(), nullable=False),
        sa.Column("bot_top_position", sa.Integer(), nullable=False),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("guild_id"),
    )


def downgrade() -> None:
    op.drop_table("guild_role_meta")
    op.drop_index("ix_guild_roles_guild_id", table_name="guild_roles")
    op.drop_table("guild_roles")
