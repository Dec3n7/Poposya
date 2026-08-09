"""bot_commands: lease-поля для восстановления зависших running-команд

Revision ID: 0041
Revises: 0040
Create Date: 2026-08-09

Добавляет `claimed_at` и `worker_id`. Раньше команда, взятая в работу
(pending->running) и не завершённая из-за краша бота, оставалась 'running'
навсегда — sweep смотрел только 'pending'. Теперь claim ставит lease
(claimed_at, worker_id, attempts+1), а sweep возвращает просроченные lease в
очередь или, исчерпав попытки, помечает failed.
"""

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bot_commands", sa.Column("claimed_at", sa.DateTime(), nullable=True))
    op.add_column("bot_commands", sa.Column("worker_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("bot_commands", "worker_id")
    op.drop_column("bot_commands", "claimed_at")
