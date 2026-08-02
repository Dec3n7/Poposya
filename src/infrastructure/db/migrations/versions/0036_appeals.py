"""апелляции на наказания (appeals)

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-02

Наказанный обжалует бан/темпбан/мут кнопкой в ЛС; модератор принимает (наказание
снимается) или отклоняет. Одна таблица: статус меняется при разборе, review-
сообщение с кнопками в канале апелляций хранится для навигации.
"""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "appeals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("original_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("review_message_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("resolver_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_appeals_guild_status", "appeals", ["guild_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_appeals_guild_status", table_name="appeals")
    op.drop_table("appeals")
