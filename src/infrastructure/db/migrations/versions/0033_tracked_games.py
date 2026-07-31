"""отслеживаемые игры Steam: тред в форуме + анонс официальных новостей

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-31

Одна таблица, параллель tracked_repos. FK на гильдию нет (модули независимы,
дружба с SQLite): целостность держим в сервисном слое. Уникальность
(guild_id, appid) — одна игра отслеживается на сервере один раз.
"""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracked_games",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("appid", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_news_gid", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_news_date", sa.DateTime(), nullable=True),
        sa.Column("added_by", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("guild_id", "appid", name="uq_tracked_game"),
    )
    op.create_index("ix_tracked_games_guild", "tracked_games", ["guild_id"])


def downgrade() -> None:
    op.drop_index("ix_tracked_games_guild", table_name="tracked_games")
    op.drop_table("tracked_games")
