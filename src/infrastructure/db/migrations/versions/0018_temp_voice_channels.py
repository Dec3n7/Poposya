"""temp_voice_channels (временные голосовые каналы — «каморки»)

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-17

"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "temp_voice_channels",
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("channel_id"),
    )
    op.create_index("ix_temp_voice_channels_guild_id", "temp_voice_channels", ["guild_id"])


def downgrade() -> None:
    op.drop_index("ix_temp_voice_channels_guild_id", table_name="temp_voice_channels")
    op.drop_table("temp_voice_channels")
