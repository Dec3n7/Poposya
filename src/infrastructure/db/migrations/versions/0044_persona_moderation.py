"""persona moderation: заявки серверов на кастомную персону

Revision ID: 0044
Revises: 0043
Create Date: 2026-08-18

Кастомная персона сервера под ручной модерацией оператора. Персона получает
статус (approved | draft | pending | rejected) и владельца-гильдию: заявка
серверного админа живёт как персона со status!=approved и owner_guild_id, но НЕ
назначается серверу, пока оператор не одобрит (бот читает только назначенные —
черновик невидим для живого бота). Существующие персоны — approved, без владельца.
"""

import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # существующие персоны (библиотека оператора + дефолт) = approved, без владельца
    op.add_column(
        "personas",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="approved"),
    )
    op.add_column("personas", sa.Column("owner_guild_id", sa.BigInteger(), nullable=True))
    op.add_column("personas", sa.Column("submitted_by", sa.BigInteger(), nullable=True))
    op.add_column(
        "personas",
        sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
    )
    # server_default нужен был только для заполнения существующих строк; дальше
    # значение проставляет приложение
    op.alter_column("personas", "status", server_default=None)
    op.alter_column("personas", "review_note", server_default=None)
    # очередь модерации: быстрый выбор заявок и черновика по владельцу
    op.create_index("ix_personas_status", "personas", ["status"])
    op.create_index("ix_personas_owner_guild", "personas", ["owner_guild_id"])


def downgrade() -> None:
    op.drop_index("ix_personas_owner_guild", table_name="personas")
    op.drop_index("ix_personas_status", table_name="personas")
    op.drop_column("personas", "review_note")
    op.drop_column("personas", "submitted_by")
    op.drop_column("personas", "owner_guild_id")
    op.drop_column("personas", "status")
