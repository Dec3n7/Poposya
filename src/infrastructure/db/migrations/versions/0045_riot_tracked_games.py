"""отслеживаемые игры Riot: тред в форуме + анонс новостей

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-20

Своя таблица (вертикаль riot независима от steam/git): колонка game готова к
будущим играм семьи, сейчас единственное значение — 'valorant'. FK на гильдию
нет (модули независимы, дружба с SQLite): целостность держим в сервисном слое.
Уникальность (guild_id, game) — одна игра отслеживается на сервере один раз.
"""

import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "riot_tracked_games",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("game", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_news_gid", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_news_date", sa.DateTime(), nullable=True),
        sa.Column("added_by", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("guild_id", "game", name="uq_riot_tracked_game"),
    )
    op.create_index("ix_riot_tracked_games_guild", "riot_tracked_games", ["guild_id"])


def downgrade() -> None:
    op.drop_index("ix_riot_tracked_games_guild", table_name="riot_tracked_games")
    op.drop_table("riot_tracked_games")
