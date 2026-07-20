"""member_roles (кто какие роли носит — зеркало для панели)

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-20

Часть зеркала ролей: строка на каждую пару участник×роль (кроме @everyone —
она у всех, хранить её бессмысленно). Нужна для счётчиков носителей и выдачи/
снятия ролей из панели. Бот держит актуальной теми же событиями, что и роли
(бэкфилл + on_member_update/join/remove).
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "member_roles",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("guild_id", "user_id", "role_id"),
    )
    # счётчики носителей роли на сервере
    op.create_index("ix_member_roles_guild_role", "member_roles", ["guild_id", "role_id"])
    # каскадное удаление, когда роль удалили
    op.create_index("ix_member_roles_role", "member_roles", ["role_id"])


def downgrade() -> None:
    op.drop_index("ix_member_roles_role", table_name="member_roles")
    op.drop_index("ix_member_roles_guild_role", table_name="member_roles")
    op.drop_table("member_roles")
