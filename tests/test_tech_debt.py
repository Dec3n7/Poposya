"""Техдолг: бэкап SQLite, Outbox критичных событий, персист войс-минут."""

import os
import shutil
import sqlite3
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from src.domain.relationship.events import ExclusiveTransferred, RelationshipRoleChanged
from src.infrastructure.db.backup import (
    PostgresBackupService,
    SqliteBackupService,
    postgres_params_from_url,
    sqlite_path_from_url,
)
from src.infrastructure.db.models.outbox import OutboxEventModel
from src.infrastructure.db.repositories.activity import SqlAlchemyVoiceProgressRepository
from src.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from src.infrastructure.events.in_memory_bus import InMemoryEventBus
from src.infrastructure.events.outbox import (
    OutboxDispatcher,
    deserialize_event,
    outbox_row_for,
    serialize_event,
)

NOW = datetime(2026, 7, 10, 23, 0, tzinfo=UTC)

# session_factory берётся из conftest: своя копия фикстуры держала бы outbox и
# войс-прогресс на SQLite даже при TEST_DATABASE_URL, а это критичная
# инфраструктура — её надо проверять на той БД, где живёт бот.
# Тесты бэкапа ниже намеренно работают с sqlite3 напрямую: сам
# SqliteBackupService умеет только SQLite, и это его контракт, а не недосмотр.


# --- бэкап SQLite ---


def test_sqlite_path_from_url():
    assert sqlite_path_from_url("sqlite+aiosqlite:///./poposya.db").name == "poposya.db"
    assert (
        str(sqlite_path_from_url("sqlite+aiosqlite:////app/data/poposya.db")).replace("\\", "/")
        == "/app/data/poposya.db"
    )
    assert sqlite_path_from_url("postgresql+asyncpg://x/y") is None


def _make_db(path) -> None:
    conn = sqlite3.connect(str(path))
    with conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (42)")
    conn.close()


def test_backup_creates_valid_copy(tmp_path):
    db = tmp_path / "poposya.db"
    _make_db(db)
    service = SqliteBackupService(f"sqlite+aiosqlite:///{db}", interval_hours=24, keep=7)
    assert service.enabled
    target = service.backup_once()
    assert target is not None and target.exists()
    conn = sqlite3.connect(str(target))
    assert conn.execute("SELECT x FROM t").fetchone() == (42,)
    conn.close()


def test_backup_prunes_old_copies(tmp_path):
    db = tmp_path / "poposya.db"
    _make_db(db)
    service = SqliteBackupService(f"sqlite+aiosqlite:///{db}", interval_hours=24, keep=2)
    # имена различаются посекундной меткой — создаём «старые» вручную
    service.backup_dir.mkdir()
    for stamp in ("20260101-000000", "20260102-000000", "20260103-000000"):
        (service.backup_dir / f"poposya-{stamp}.db").write_bytes(b"old")
    service.backup_once()
    remaining = sorted(p.name for p in service.backup_dir.glob("poposya-*.db"))
    assert len(remaining) == 2
    assert "poposya-20260101-000000.db" not in remaining
    assert "poposya-20260102-000000.db" not in remaining


def test_backup_disabled_for_non_sqlite():
    service = SqliteBackupService("postgresql+asyncpg://x/y", 24, 7)
    assert not service.enabled


# --- бэкап Postgres (pg_dump) ---


def test_postgres_params_strips_driver_and_parses():
    p = postgres_params_from_url("postgresql+asyncpg://poposya:s3cret@db:5432/poposya")
    assert p == {
        "host": "db",
        "port": "5432",
        "user": "poposya",
        "password": "s3cret",
        "dbname": "poposya",
    }


def test_postgres_params_default_port_and_url_decoding():
    p = postgres_params_from_url("postgresql://u%40x:p%2Fw@host/mydb")  # user u@x, pass p/w
    assert p["port"] == "5432"  # порт не указан — дефолтный
    assert p["user"] == "u@x" and p["password"] == "p/w"  # %-декодирование


def test_postgres_params_none_for_non_postgres_or_incomplete():
    assert postgres_params_from_url("sqlite+aiosqlite:///./p.db") is None
    assert postgres_params_from_url("postgresql://host_only_no_db") is None


def test_postgres_backup_enabled_gating():
    ok = PostgresBackupService("postgresql+asyncpg://u:p@h/d", "data/backups", 24, 7)
    assert ok.enabled
    assert not PostgresBackupService("sqlite:///x.db", "data/backups", 24, 7).enabled
    assert not PostgresBackupService("postgresql://u:p@h/d", "data/backups", 0, 7).enabled
    assert not PostgresBackupService("postgresql://u:p@h/d", "data/backups", 24, 0).enabled


@pytest.mark.skipif(
    shutil.which("pg_dump") is None
    or not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="нужен pg_dump в PATH и TEST_DATABASE_URL на Postgres",
)
def test_postgres_backup_once_writes_restorable_dump(tmp_path):
    url = os.environ["TEST_DATABASE_URL"]
    service = PostgresBackupService(url, str(tmp_path / "backups"), interval_hours=24, keep=7)
    assert service.enabled
    target = service.backup_once()
    assert target is not None and target.exists() and target.stat().st_size > 0
    # -Fc даёт custom-формат: первые байты — сигнатура "PGDMP"
    assert target.read_bytes()[:5] == b"PGDMP"


@pytest.mark.skipif(
    shutil.which("pg_dump") is None
    or not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="нужен pg_dump в PATH и TEST_DATABASE_URL на Postgres",
)
def test_postgres_backup_prunes_old_dumps(tmp_path):
    url = os.environ["TEST_DATABASE_URL"]
    service = PostgresBackupService(url, str(tmp_path / "backups"), interval_hours=24, keep=2)
    service.backup_dir.mkdir(parents=True)
    dbname = postgres_params_from_url(url)["dbname"]
    for stamp in ("20260101-000000", "20260102-000000", "20260103-000000"):
        (service.backup_dir / f"{dbname}-{stamp}.dump").write_bytes(b"old")
    service.backup_once()  # +1 свежий, keep=2 -> остаётся 2
    remaining = sorted(p.name for p in service.backup_dir.glob(f"{dbname}-*.dump"))
    assert len(remaining) == 2
    assert f"{dbname}-20260101-000000.dump" not in remaining


# --- outbox ---


def test_event_serialization_roundtrip():
    event = RelationshipRoleChanged(
        aggregate_id="10:1",
        guild_id=10,
        user_id=1,
        channel_id=5,
        old_role_index=None,
        new_role_index=2,
        points=450,
    )
    restored = deserialize_event(event.event_type, serialize_event(event))
    assert restored == event

    transfer = ExclusiveTransferred(
        aggregate_id="10",
        guild_id=10,
        new_user_id=2,
        previous_user_id=1,
    )
    assert deserialize_event(transfer.event_type, serialize_event(transfer)) == transfer


def test_unknown_event_type_returns_none():
    assert deserialize_event("nope.unknown", "{}") is None


async def test_uow_stores_critical_event_and_marks_published(session_factory):
    bus = InMemoryEventBus()
    received = []

    async def handler(e):
        received.append(e)

    bus.subscribe(RelationshipRoleChanged, handler)

    uow = SqlAlchemyUnitOfWork(session_factory, bus)
    event = RelationshipRoleChanged(
        aggregate_id="10:1",
        guild_id=10,
        user_id=1,
        new_role_index=1,
        points=250,
    )
    async with uow:
        uow.add_event(event)
        await uow.commit()

    async with session_factory() as session:
        row = (await session.execute(select(OutboxEventModel))).scalar_one()
        assert row.event_id == str(event.event_id)
        assert row.published_at is not None  # опубликовано и помечено


async def test_dispatcher_delivers_unpublished(session_factory):
    event = RelationshipRoleChanged(
        aggregate_id="10:1",
        guild_id=10,
        user_id=1,
        new_role_index=3,
        points=700,
    )
    # имитация падения между commit и publish: запись есть, published_at пуст
    async with session_factory() as session:
        session.add(outbox_row_for(event))
        await session.commit()

    bus = InMemoryEventBus()
    received = []

    async def handler(e):
        received.append(e)

    bus.subscribe(RelationshipRoleChanged, handler)
    dispatcher = OutboxDispatcher(session_factory, bus, interval_seconds=1)
    assert await dispatcher.dispatch_once() == 1
    assert received[0] == event
    # повторный проход ничего не шлёт — событие помечено
    assert await dispatcher.dispatch_once() == 0


# --- войс-прогресс ---


async def test_voice_progress_roundtrip(session_factory):
    async with session_factory() as session:
        repo = SqlAlchemyVoiceProgressRepository(session)
        await repo.save_many({(10, 1): 25.0, (10, 2): 55.0})
        await session.commit()

    async with session_factory() as session:
        repo = SqlAlchemyVoiceProgressRepository(session)
        loaded = await repo.load_all()
        assert loaded == {(10, 1): 25.0, (10, 2): 55.0}
        # upsert обновляет существующую строку
        await repo.save_many({(10, 1): 40.0})
        await session.commit()

    async with session_factory() as session:
        repo = SqlAlchemyVoiceProgressRepository(session)
        loaded = await repo.load_all()
        assert loaded[(10, 1)] == 40.0


async def test_voice_totals_accrue(session_factory):
    async with session_factory() as session:
        repo = SqlAlchemyVoiceProgressRepository(session)
        # два тика по 5 минут: остаток перезаписывается, итог копится
        await repo.save_many({(10, 1): 5.0}, accrued_minutes=5.0)
        await repo.save_many({(10, 1): 10.0}, accrued_minutes=5.0)
        # начисление очков сбросило остаток — итог не трогается
        await repo.save_many({(10, 1): 0.0})
        await session.commit()

    async with session_factory() as session:
        repo = SqlAlchemyVoiceProgressRepository(session)
        assert await repo.total_minutes(10, 1) == 10.0
        assert await repo.total_minutes(10, 99) == 0.0
