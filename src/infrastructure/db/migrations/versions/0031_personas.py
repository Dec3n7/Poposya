"""персоны: библиотеки текста/личности бота с назначением на сервер

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-21

Три таблицы (аналогия с guild_settings — «в БД только override»):
- personas: именованная библиотека. Дефолтная «Попося» (is_default) неудаляема.
  prompt/chime_prompt/attributes ПУСТЫЕ у дефолта = резолв из кода/файла (реестр
  PHRASE_SPECS и файлы промптов остаются источником правды дефолта).
- persona_phrases: построчный override каталога фраз (key из PHRASE_SPECS);
  NULL-строки нет — отсутствие строки = дефолт из реестра.
- guild_persona: назначение персоны серверу (одна активная на гильдию).

FK намеренно не используем (как и везде в проекте — модули независимы, дружба
с SQLite): целостность persona_id держим в сервисном слое. Сид одной строки
дефолт-персоны здесь, а не на старте бота, — миграция однопоточна, без гонки
двух писателей (бот ∥ веб-панель).
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "personas",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("chime_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("attributes", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "persona_phrases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("persona_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),  # JSON: str | list | dict
        sa.Column("mode", sa.Text(), nullable=False, server_default="ai_then_static"),
        sa.UniqueConstraint("persona_id", "key", name="uq_persona_phrase"),
    )
    op.create_table(
        "guild_persona",
        sa.Column("guild_id", sa.BigInteger(), primary_key=True),
        sa.Column("persona_id", sa.Integer(), nullable=False),
    )

    # Сид дефолтной «Попоси»: пустые prompt/chime_prompt/attributes = резолв из
    # кода (файлы промптов + DEFAULT_ATTRIBUTES). Строка нужна как запись, которую
    # можно назначать серверу и от которой «дублировать».
    now = datetime.now(UTC).replace(tzinfo=None)
    personas = sa.table(
        "personas",
        sa.column("name", sa.Text),
        sa.column("is_default", sa.Boolean),
        sa.column("prompt", sa.Text),
        sa.column("chime_prompt", sa.Text),
        sa.column("attributes", sa.Text),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    op.bulk_insert(
        personas,
        [
            {
                "name": "Попося",
                "is_default": True,
                "prompt": "",
                "chime_prompt": "",
                "attributes": "{}",
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("guild_persona")
    op.drop_table("persona_phrases")
    op.drop_table("personas")
