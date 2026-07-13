"""secret_codes + secret_rooms

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-09

"""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "secret_codes",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("guild_id", "user_id"),
    )
    op.create_index("ix_secret_codes_code", "secret_codes", ["guild_id", "code"])

    op.create_table(
        "secret_rooms",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("text_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("voice_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_secret_rooms_expires", "secret_rooms", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_secret_rooms_expires", table_name="secret_rooms")
    op.drop_table("secret_rooms")
    op.drop_index("ix_secret_codes_code", table_name="secret_codes")
    op.drop_table("secret_codes")
