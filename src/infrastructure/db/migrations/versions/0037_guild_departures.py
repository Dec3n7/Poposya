"""приватность: отметки выхода бота с сервера для отложенного удаления данных

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-04

Одна таблица `guild_departures` (guild_id PK, left_at). При on_guild_remove
ставится строка, при on_guild_join снимается; фоновый цикл стирает данные
сервера, когда отметка старше окна отсрочки. Само удаление данных — по уже
существующим таблицам, новых столбцов не требует.
"""

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guild_departures",
        sa.Column("guild_id", sa.BigInteger(), primary_key=True),
        sa.Column("left_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("guild_departures")
