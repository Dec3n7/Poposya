"""серверный отзыв веб-сессий: эпоха сессий на пользователя

Revision ID: 0038
Revises: 0037
Create Date: 2026-08-05

Одна таблица `web_session_epoch(user_id, epoch)`. Токен несёт эпоху; бамп
(real logout / операторский отзыв) гасит все токены пользователя. Отсутствие
строки = эпоха 0 (совместимо с ранее выданными токенами).
"""

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "web_session_epoch",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("epoch", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("web_session_epoch")
