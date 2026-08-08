"""Перенос данных SQLite → PostgreSQL.

Проверяем портируемую часть на паре SQLite→SQLite (всегда): копия идёт в
FK-порядке, типы (boolean, date, datetime) переживают перенос, а защиты
(непустая цель, отсутствие схемы, расхождение ревизий) срабатывают ДО первой
записи. PostgreSQL-специфику (сброс sequence) гоняем отдельным тестом, только
когда задан TEST_DATABASE_URL — как и весь остальной набор.
"""

import os
from datetime import date, datetime

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from src.infrastructure.db import migrate_sqlite_to_pg as mig
from src.infrastructure.db.models.base import Base


def _sqlite_url(tmp_path, name: str) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


async def _create_schema(url: str) -> None:
    mig.discover_models()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _stamp_revision(url: str, revision: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        await conn.execute(text("INSERT INTO alembic_version VALUES (:r)"), {"r": revision})
    await engine.dispose()


def _t(name: str):
    return Base.metadata.tables[name]


# ── чистые функции ────────────────────────────────────────────────────────────


def test_normalize_source_accepts_path_and_url():
    assert mig.normalize_source("./poposya.db") == "sqlite+aiosqlite:///./poposya.db"
    url = "postgresql+asyncpg://u:p@h:5432/db"
    assert mig.normalize_source(url) == url


def test_discover_models_populates_metadata_beyond_env_py():
    """env.py импортирует лишь часть модулей — мигратор обязан видеть ВСЕ.
    bot_commands (commands.py) и panel_audit (audit.py) в env.py не перечислены."""
    mig.discover_models()
    names = set(Base.metadata.tables)
    assert {"bot_commands", "panel_audit", "relationship_profiles"} <= names
    assert len(names) >= 40


# ── перенос данных ────────────────────────────────────────────────────────────


async def test_migrate_copies_rows_and_preserves_types(tmp_path):
    src_url = _sqlite_url(tmp_path, "src.db")
    dst_url = _sqlite_url(tmp_path, "dst.db")
    await _create_schema(src_url)
    await _create_schema(dst_url)

    prof, room = _t("relationship_profiles"), _t("secret_rooms")
    src = create_async_engine(src_url)
    async with src.begin() as conn:
        await conn.execute(
            insert(prof),
            [
                {
                    "user_id": 1,
                    "guild_id": 10,
                    "points": 700,
                    "is_exclusive": True,
                    "last_award_date": date(2026, 8, 1),
                },
                {
                    "user_id": 2,
                    "guild_id": 10,
                    "points": 50,
                    "is_exclusive": False,
                    "last_award_date": None,
                },
            ],
        )
        await conn.execute(
            insert(room),
            [
                {
                    "guild_id": 10,
                    "text_channel_id": 111,
                    "voice_channel_id": 222,
                    "expires_at": datetime(2026, 8, 8, 12, 0, 0),
                    "created_by": 1,
                }
            ],
        )
    await src.dispose()

    report = await mig.migrate(src_url, dst_url)
    assert report["relationship_profiles"] == 2
    assert report["secret_rooms"] == 1

    dst = create_async_engine(dst_url)
    async with dst.connect() as conn:
        rows = (
            await conn.execute(
                select(prof.c.is_exclusive, prof.c.last_award_date).order_by(prof.c.user_id)
            )
        ).all()
        assert rows[0].is_exclusive is True  # boolean, а не 1
        assert rows[0].last_award_date == date(2026, 8, 1)
        assert rows[1].is_exclusive is False
        r = (await conn.execute(select(room.c.id, room.c.expires_at))).one()
        assert r.id == 1 and r.expires_at == datetime(2026, 8, 8, 12, 0, 0)
    await dst.dispose()


async def test_migrate_refuses_nonempty_dest_then_force_appends(tmp_path):
    src_url = _sqlite_url(tmp_path, "src.db")
    dst_url = _sqlite_url(tmp_path, "dst.db")
    await _create_schema(src_url)
    await _create_schema(dst_url)

    prof = _t("relationship_profiles")
    src = create_async_engine(src_url)
    async with src.begin() as conn:
        await conn.execute(insert(prof), [{"user_id": 1, "guild_id": 9}])
    await src.dispose()
    dst = create_async_engine(dst_url)
    async with dst.begin() as conn:  # уже есть другой профиль
        await conn.execute(insert(prof), [{"user_id": 9, "guild_id": 9}])
    await dst.dispose()

    with pytest.raises(mig.MigrationError, match="не пусты"):
        await mig.migrate(src_url, dst_url)

    report = await mig.migrate(src_url, dst_url, force=True)
    assert report["relationship_profiles"] == 1  # дописал исходную строку


async def test_migrate_requires_dest_schema(tmp_path):
    src_url = _sqlite_url(tmp_path, "src.db")
    dst_url = _sqlite_url(tmp_path, "empty.db")
    await _create_schema(src_url)
    empty = create_async_engine(dst_url)  # файл есть, схемы нет
    async with empty.begin() as conn:
        await conn.execute(text("SELECT 1"))
    await empty.dispose()

    with pytest.raises(mig.MigrationError, match="нет части таблиц"):
        await mig.migrate(src_url, dst_url)


async def test_migrate_detects_revision_mismatch(tmp_path):
    src_url = _sqlite_url(tmp_path, "src.db")
    dst_url = _sqlite_url(tmp_path, "dst.db")
    await _create_schema(src_url)
    await _create_schema(dst_url)
    await _stamp_revision(src_url, "0001_old")
    await _stamp_revision(dst_url, "0002_new")

    with pytest.raises(mig.MigrationError, match="ревизии"):
        await mig.migrate(src_url, dst_url)


def test_main_rejects_non_postgres_dest(capsys):
    code = mig.main(["--source", "x.db", "--dest", "sqlite+aiosqlite:///y.db"])
    assert code == 2
    assert "postgresql" in capsys.readouterr().err


# ── PostgreSQL-специфика (только при TEST_DATABASE_URL=postgresql…) ────────────


@pytest.mark.skipif(
    not (os.environ.get("TEST_DATABASE_URL") or "").startswith("postgresql"),
    reason="нужен PostgreSQL (TEST_DATABASE_URL)",
)
async def test_migrate_into_postgres_resets_sequences(tmp_path):
    """После переноса строк с явными id sequence обязан быть подтянут: первый же
    insert приложения должен получить следующий id, а не столкнуться с занятым."""
    src_url = _sqlite_url(tmp_path, "src.db")
    dst_url = os.environ["TEST_DATABASE_URL"]
    await _create_schema(src_url)

    dst = create_async_engine(dst_url)
    async with dst.begin() as conn:  # чистая схема в целевой PG
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await dst.dispose()

    room = _t("secret_rooms")
    row = {
        "guild_id": 10,
        "text_channel_id": 111,
        "voice_channel_id": 222,
        "expires_at": datetime(2026, 8, 8, 12, 0, 0),
        "created_by": 1,
    }
    src = create_async_engine(src_url)
    async with src.begin() as conn:
        await conn.execute(insert(room), [{**row, "id": 1}, {**row, "id": 2}])
    await src.dispose()

    await mig.migrate(src_url, dst_url)

    dst = create_async_engine(dst_url)
    async with dst.begin() as conn:
        new_id = (await conn.execute(insert(room).returning(room.c.id), row)).scalar_one()
        assert new_id == 3  # sequence сдвинут за максимум перенесённых
    await dst.dispose()
