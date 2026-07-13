"""Техдолг: бэкап SQLite, Outbox критичных событий, персист войс-минут."""

import sqlite3
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain.relationship.events import ExclusiveTransferred, RelationshipRoleChanged
from src.infrastructure.db.backup import SqliteBackupService, sqlite_path_from_url
from src.infrastructure.db.models.base import Base
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


@pytest.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


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
