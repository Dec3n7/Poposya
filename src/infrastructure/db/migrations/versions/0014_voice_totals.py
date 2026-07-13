"""суммарное время в войсе для профиля-витрины

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-11

"""

from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("voice_progress") as batch_op:
        batch_op.add_column(
            sa.Column("total_minutes", sa.Float(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("voice_progress") as batch_op:
        batch_op.drop_column("total_minutes")
