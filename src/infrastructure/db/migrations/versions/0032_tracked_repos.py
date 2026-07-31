"""отслеживаемые GitHub-репозитории: тред в форуме + анонс новых релизов

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-31

Одна таблица. FK на гильдию намеренно нет (как и везде — модули независимы,
дружба с SQLite): целостность держим в сервисном слое. Уникальность
(guild_id, owner, name) — один репозиторий отслеживается на сервере один раз.
"""

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracked_repos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_release_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_published_at", sa.DateTime(), nullable=True),
        sa.Column("etag", sa.Text(), nullable=False, server_default=""),
        sa.Column("added_by", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("guild_id", "owner", "name", name="uq_tracked_repo"),
    )
    op.create_index("ix_tracked_repos_guild", "tracked_repos", ["guild_id"])


def downgrade() -> None:
    op.drop_index("ix_tracked_repos_guild", table_name="tracked_repos")
    op.drop_table("tracked_repos")
