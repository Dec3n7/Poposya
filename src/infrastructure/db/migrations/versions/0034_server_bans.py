"""кросс-серверные баны: общая таблица банов по всем серверам бота

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-31

Одна таблица. Собираются баны со ВСЕХ серверов (любые, не только бота), чтобы
показать модератору в панели кросс-серверную картину. Наружу в Discord не идёт.
Уникальность (guild_id, user_id) — один бан на сервер на пользователя.
"""

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "server_bans",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("banned_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("guild_id", "user_id", name="uq_server_ban"),
    )
    op.create_index("ix_server_bans_user", "server_bans", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_server_bans_user", table_name="server_bans")
    op.drop_table("server_bans")
