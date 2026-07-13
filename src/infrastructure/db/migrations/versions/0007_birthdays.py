"""день рождения в relationship_profiles

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-09

"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("relationship_profiles") as batch_op:
        batch_op.add_column(sa.Column("birthday_day", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("birthday_month", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("birthday_reminded_at", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("birthday_congratulated_at", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("relationship_profiles") as batch_op:
        batch_op.drop_column("birthday_congratulated_at")
        batch_op.drop_column("birthday_reminded_at")
        batch_op.drop_column("birthday_month")
        batch_op.drop_column("birthday_day")
