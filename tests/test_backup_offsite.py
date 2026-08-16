"""Off-box выгрузка бэкапов (src/infrastructure/db/backup.py).

Локальные дампы лежат на том же боксе, что и БД, — отказ провайдера унёс бы и то,
и другое. Выгрузка гонит каждый свежий дамп наружу настраиваемой командой. Здесь
проверяем разбор команды, подстановку пути, что провал выгрузки НЕ рушит цикл
бэкапов, и что сервис получает выгрузчик только при заданной команде.
"""

import os
from pathlib import Path

import pytest

from src.infrastructure.db.backup import (
    PostgresBackupService,
    SqliteBackupService,
    command_offsite_uploader,
    make_backup_service,
    offsite_argv,
    ship_offsite,
)

_PG_URL = "postgresql+asyncpg://u:p@h:5432/db"


# ── разбор команды ────────────────────────────────────────────────────────────


def test_argv_substitutes_placeholder():
    assert offsite_argv("rclone copy {path} remote:bucket", Path("dump.dump")) == [
        "rclone",
        "copy",
        "dump.dump",
        "remote:bucket",
    ]


def test_argv_appends_path_when_no_placeholder():
    # без {path} путь становится последним аргументом
    assert offsite_argv("rclone copy remote:bucket", Path("dump.dump")) == [
        "rclone",
        "copy",
        "remote:bucket",
        "dump.dump",
    ]


def test_empty_command_disables_offsite():
    assert command_offsite_uploader("") is None
    assert command_offsite_uploader("   ") is None


def test_nonempty_command_builds_uploader():
    assert callable(command_offsite_uploader("scp {path} host:/backups/"))


# ── устойчивость выгрузки ─────────────────────────────────────────────────────


def test_ship_offsite_noop_when_disabled():
    ship_offsite(None, Path("dump.dump"))  # просто не падает


def test_ship_offsite_calls_uploader_with_path():
    seen: list[Path] = []
    ship_offsite(seen.append, Path("dump.dump"))
    assert seen == [Path("dump.dump")]


def test_ship_offsite_swallows_uploader_errors():
    def boom(_: Path) -> None:
        raise RuntimeError("сеть отвалилась")

    # провал выгрузки НЕ должен подниматься наверх — иначе рвётся цикл бэкапов
    ship_offsite(boom, Path("dump.dump"))


# ── проводка в сервисы ────────────────────────────────────────────────────────


def test_service_gets_uploader_only_when_command_set():
    with_cmd = make_backup_service(_PG_URL, "d", 24, 7, "rclone copy {path} r:b")
    assert isinstance(with_cmd, PostgresBackupService)
    assert with_cmd._offsite is not None

    without = make_backup_service(_PG_URL, "d", 24, 7, "")
    assert isinstance(without, PostgresBackupService)
    assert without._offsite is None


def test_sqlite_service_also_wires_offsite(tmp_path):
    svc = make_backup_service(
        f"sqlite+aiosqlite:///{tmp_path / 'x.db'}", "d", 24, 7, "scp {path} host:/b/"
    )
    assert isinstance(svc, SqliteBackupService)
    assert svc._offsite is not None


# ── реальный subprocess (posix-CI: true/false как детерминированные коды) ──────


@pytest.mark.skipif(os.name == "nt", reason="posix-only хелперы true/false")
def test_uploader_runs_command_and_raises_on_failure(tmp_path):
    dump = tmp_path / "db.dump"
    dump.write_text("x")

    ok = command_offsite_uploader("true {path}")
    assert ok is not None
    ok(dump)  # код 0 — без исключения

    bad = command_offsite_uploader("false {path}")
    assert bad is not None
    with pytest.raises(RuntimeError):
        bad(dump)  # ненулевой код → RuntimeError (его глушит ship_offsite)
