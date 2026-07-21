"""guild_role_templates (сохранённые наборы ролей панели)

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-21

Пер-серверные именованные шаблоны ролей: администратор сохраняет текущий набор
редактируемых ролей (имя/цвет/hoist/mentionable, без прав) и применяет одним
кликом позже. payload — JSON-список ролей. Уникальность (guild_id, name):
повторное сохранение под тем же именем обновляет шаблон.
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guild_role_templates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),  # JSON-список ролей
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("guild_id", "name", name="uq_role_template_guild_name"),
    )
    op.create_index("ix_role_templates_guild", "guild_role_templates", ["guild_id"])


def downgrade() -> None:
    op.drop_index("ix_role_templates_guild", table_name="guild_role_templates")
    op.drop_table("guild_role_templates")
