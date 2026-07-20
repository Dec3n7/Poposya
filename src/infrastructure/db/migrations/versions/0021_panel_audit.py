"""panel_audit (журнал действий веб-панели)

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-19

"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "panel_audit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_panel_audit_guild", "panel_audit", ["guild_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_panel_audit_guild", table_name="panel_audit")
    op.drop_table("panel_audit")
