"""Техдолг: бэкап SQLite, Outbox критичных событий, персист войс-минут."""

import asyncio
import contextlib
import os
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from src.domain.relationship.events import ExclusiveTransferred, RelationshipRoleChanged
from src.infrastructure.db import backup as backup_module
from src.infrastructure.db.backup import (
    PostgresBackupService,
    SqliteBackupService,
    make_backup_service,
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


def test_sqlite_path_from_url_without_triple_slash_is_malformed():
    assert sqlite_path_from_url("sqlite+aiosqlite://") is None


def test_make_backup_service_dispatches_by_url(tmp_path):
    sqlite_service = make_backup_service(
        f"sqlite+aiosqlite:///{tmp_path / 'p.db'}", "data/backups", 24, 7
    )
    assert isinstance(sqlite_service, SqliteBackupService)
    postgres_service = make_backup_service("postgresql+asyncpg://u:p@h/d", "data/backups", 24, 7)
    assert isinstance(postgres_service, PostgresBackupService)
    assert make_backup_service("mysql://u:p@h/d", "data/backups", 24, 7) is None


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


def test_backup_once_missing_db_file_returns_none(tmp_path):
    service = SqliteBackupService(f"sqlite+aiosqlite:///{tmp_path / 'never-created.db'}", 24, 7)
    assert service.backup_once() is None


def test_sqlite_prune_swallows_unlink_errors(tmp_path, monkeypatch, caplog):
    db = tmp_path / "poposya.db"
    _make_db(db)
    service = SqliteBackupService(f"sqlite+aiosqlite:///{db}", interval_hours=24, keep=1)
    service.backup_dir.mkdir()
    for stamp in ("20260101-000000", "20260102-000000"):
        (service.backup_dir / f"poposya-{stamp}.db").write_bytes(b"old")

    def raising_unlink(self, *a, **kw):
        raise OSError("файл занят другим процессом")

    monkeypatch.setattr(Path, "unlink", raising_unlink)
    with caplog.at_level("WARNING"):
        service._prune()  # не падает — старые копии просто остаются
    assert "Не удалось удалить" in caplog.text
    remaining = list(service.backup_dir.glob("poposya-*.db"))
    assert len(remaining) == 2  # ничего не удалилось


async def test_sqlite_run_forever_backs_up_repeatedly_and_survives_errors(tmp_path):
    db = tmp_path / "poposya.db"
    _make_db(db)
    # interval_hours=0 -> sleep(0) между итерациями, цикл крутится без реального ожидания
    service = SqliteBackupService(f"sqlite+aiosqlite:///{db}", interval_hours=0, keep=7)

    calls = 0
    real_backup_once = service.backup_once

    def flaky_backup_once():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("диск занят")
        return real_backup_once()

    service.backup_once = flaky_backup_once

    task = asyncio.create_task(service.run_forever())
    try:
        deadline = asyncio.get_event_loop().time() + 3.0
        while calls < 3 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert calls >= 3  # ошибка на второй итерации не остановила цикл


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


def test_postgres_backup_dir_property():
    service = PostgresBackupService("postgresql://u:p@h/d", "some/dir", 24, 7)
    assert service.backup_dir == Path("some/dir")


def test_postgres_backup_once_returns_none_when_params_missing(tmp_path):
    service = PostgresBackupService("sqlite:///x.db", str(tmp_path), 24, 7)
    assert service.backup_once() is None


# --- бэкап Postgres: логика backup_once/prune/run_forever без реального
# pg_dump (subprocess.run подменён) — сквозная проверка с настоящим бинарником
# ниже, под skipif; здесь про то, что сервис делает с результатом процесса.


def test_postgres_backup_once_success_invokes_pg_dump_and_prunes(tmp_path, monkeypatch):
    url = "postgresql+asyncpg://poposya:s3cret@dbhost:5432/poposya"
    service = PostgresBackupService(url, str(tmp_path / "backups"), interval_hours=24, keep=2)
    service.backup_dir.mkdir(parents=True)
    for stamp in ("20260101-000000", "20260102-000000"):
        (service.backup_dir / f"poposya-{stamp}.dump").write_bytes(b"old")

    captured = {}

    def fake_run(args, env=None, capture_output=None, text=None, timeout=None):
        captured["args"] = args
        captured["env"] = env
        captured["timeout"] = timeout
        target = Path(args[args.index("-f") + 1])
        target.write_bytes(b"PGDMP-fake")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(backup_module.subprocess, "run", fake_run)
    target = service.backup_once()

    assert target is not None and target.exists()
    assert target.read_bytes() == b"PGDMP-fake"
    args = captured["args"]
    assert args[0] == "pg_dump" and "-Fc" in args
    assert "dbhost" in args and "5432" in args and "poposya" in args
    assert captured["env"]["PGPASSWORD"] == "s3cret"
    assert captured["timeout"] == backup_module._PG_DUMP_TIMEOUT_SECONDS

    remaining = sorted(p.name for p in service.backup_dir.glob("poposya-*.dump"))
    assert len(remaining) == 2  # keep=2: самый старый из трёх выпилен
    assert "poposya-20260101-000000.dump" not in remaining


def test_postgres_backup_once_failure_cleans_up_and_raises(tmp_path, monkeypatch):
    url = "postgresql+asyncpg://u:p@h/d"
    service = PostgresBackupService(url, str(tmp_path / "backups"), interval_hours=24, keep=7)

    def fake_run(args, **kw):
        target = Path(args[args.index("-f") + 1])
        target.write_bytes(b"partial")  # pg_dump успел что-то написать до падения
        return SimpleNamespace(returncode=1, stderr="connection refused" * 50)

    monkeypatch.setattr(backup_module.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="pg_dump упал"):
        service.backup_once()

    assert list(service.backup_dir.glob("*.dump")) == []  # битый файл подчищен


def test_postgres_prune_swallows_unlink_errors(tmp_path, monkeypatch, caplog):
    service = PostgresBackupService("postgresql://u:p@h/d", str(tmp_path / "backups"), 24, keep=1)
    service.backup_dir.mkdir(parents=True)
    for stamp in ("20260101-000000", "20260102-000000"):
        (service.backup_dir / f"d-{stamp}.dump").write_bytes(b"old")

    def raising_unlink(self, *a, **kw):
        raise OSError("занято")

    monkeypatch.setattr(Path, "unlink", raising_unlink)
    with caplog.at_level("WARNING"):
        service._prune()  # не падает — старые копии просто остаются
    assert "Не удалось удалить" in caplog.text
    assert len(list(service.backup_dir.glob("d-*.dump"))) == 2


async def test_postgres_run_forever_backs_up_repeatedly_and_survives_errors(tmp_path):
    service = PostgresBackupService(
        "postgresql://u:p@h/d", str(tmp_path / "backups"), interval_hours=0, keep=7
    )
    calls = 0

    def flaky_backup_once():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("pg_dump упал")
        return tmp_path / f"dump-{calls}.dump"  # успешный вызов -> залогирован путь

    service.backup_once = flaky_backup_once

    task = asyncio.create_task(service.run_forever())
    try:
        deadline = asyncio.get_event_loop().time() + 3.0
        while calls < 3 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert calls >= 3  # ошибка на второй итерации не остановила цикл


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
