"""память диалогов + глубина + угасание очков

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-09

"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("relationship_profiles") as batch_op:
        batch_op.add_column(
            sa.Column("deep_dialogs", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("last_decay_at", sa.Date(), nullable=True))

    op.create_table(
        "dialog_summaries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dialog_summaries_user", "dialog_summaries",
        ["guild_id", "user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_dialog_summaries_user", table_name="dialog_summaries")
    op.drop_table("dialog_summaries")
    with op.batch_alter_table("relationship_profiles") as batch_op:
        batch_op.drop_column("last_decay_at")
        batch_op.drop_column("deep_dialogs")
