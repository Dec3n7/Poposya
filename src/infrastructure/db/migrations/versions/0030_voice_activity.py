"""voice_activity (почасовое присутствие в голосовых каналах)

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-21

Второй источник для хитмапа «активность по часам»: время в войсе. Строка на
(guild_id, дата, час UTC), значение — суммарные человеко-секунды присутствия.
Та же приватная агрегат-схема, что и message_activity: без пользователя и
содержимого. Бот копит в памяти и доливает пачкой (upsert seconds += delta).
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_activity",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("bucket_date", sa.Date(), nullable=False),
        sa.Column("bucket_hour", sa.SmallInteger(), nullable=False),
        sa.Column("seconds", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("guild_id", "bucket_date", "bucket_hour"),
    )


def downgrade() -> None:
    op.drop_table("voice_activity")
