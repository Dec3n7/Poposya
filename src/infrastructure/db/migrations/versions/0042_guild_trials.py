"""guild_trials: одноразовый пробный период (нельзя переиспользовать)

Revision ID: 0042
Revises: 0041
Create Date: 2026-08-10

Отдельная таблица, а не колонка в guild_entitlements: та строка удаляется при
revoke, а факт использования триала обязан пережить revoke — иначе
«триал → revoke → снова триал» = бесконечный Premium. Enforcement серверный.
"""

import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guild_trials",
        sa.Column("guild_id", sa.BigInteger(), primary_key=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("granted_by", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("guild_trials")
