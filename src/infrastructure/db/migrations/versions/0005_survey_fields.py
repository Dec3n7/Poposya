"""анкета знакомства в relationship_profiles

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-09

"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("relationship_profiles") as batch_op:
        batch_op.add_column(
            sa.Column("survey_gender", sa.Text(), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("survey_contact", sa.Text(), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("survey_interests", sa.Text(), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("survey_season", sa.Text(), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("survey_completed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("relationship_profiles") as batch_op:
        batch_op.drop_column("survey_completed_at")
        batch_op.drop_column("survey_season")
        batch_op.drop_column("survey_interests")
        batch_op.drop_column("survey_contact")
        batch_op.drop_column("survey_gender")
