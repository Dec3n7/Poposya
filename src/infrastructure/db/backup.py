import asyncio
import logging
import os
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

# страховка от зависшего pg_dump: поток executor'а иначе висел бы вечно
_PG_DUMP_TIMEOUT_SECONDS = 600


def sqlite_path_from_url(database_url: str) -> Path | None:
    """sqlite+aiosqlite:///./poposya.db -> ./poposya.db; не-SQLite -> None."""
    if not database_url.startswith("sqlite"):
        return None
    _, sep, tail = database_url.partition("///")
    if not sep or not tail:
        return None
    return Path(tail)


def postgres_params_from_url(database_url: str) -> dict[str, str] | None:
    """postgresql+asyncpg://user:pass@host:port/db -> параметры для pg_dump.
    Драйвер (+asyncpg/+psycopg) отбрасываем — libpq его не понимает.
    Не-Postgres или без хоста/имени БД -> None."""
    scheme, sep, rest = database_url.partition("://")
    if not sep or not scheme.startswith("postgresql"):
        return None
    parsed = urlparse("postgresql://" + rest)
    dbname = parsed.path.lstrip("/")
    if not parsed.hostname or not dbname:
        return None
    return {
        "host": parsed.hostname,
        "port": str(parsed.port or 5432),
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "dbname": dbname,
    }


def make_backup_service(database_url: str, backup_dir: str, interval_hours: int, keep: int):
    """Правильный сервис бэкапа под тип БД; None — тип неизвестен (диагностика
    об этом предупредит отдельно)."""
    if database_url.startswith("sqlite"):
        return SqliteBackupService(database_url, interval_hours, keep)
    if database_url.startswith("postgresql"):
        return PostgresBackupService(database_url, backup_dir, interval_hours, keep)
    return None


class SqliteBackupService:
    """Периодическая копия базы штатным online backup API SQLite — безопасно
    на живой БД, в отличие от копирования файла (риск порванной страницы).
    Бэкапы лежат в <каталог БД>/backups (в Docker это volume bot_data),
    старые ротируются, хранится keep последних."""

    def __init__(self, database_url: str, interval_hours: int, keep: int):
        self._db_path = sqlite_path_from_url(database_url)
        self._interval_seconds = interval_hours * 3600
        self._keep = keep

    @property
    def enabled(self) -> bool:
        return self._db_path is not None and self._interval_seconds > 0 and self._keep > 0

    @property
    def backup_dir(self) -> Path:
        return self._db_path.parent / "backups"

    def backup_once(self) -> Path | None:
        """Синхронный бэкап (вызывается из executor). Возвращает путь копии."""
        if self._db_path is None or not self._db_path.exists():
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        target = self.backup_dir / f"{self._db_path.stem}-{stamp}.db"
        source = sqlite3.connect(str(self._db_path))
        try:
            destination = sqlite3.connect(str(target))
            try:
                with destination:
                    source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()
        self._prune()
        return target

    def _prune(self) -> None:
        backups = sorted(self.backup_dir.glob(f"{self._db_path.stem}-*.db"))
        for stale in backups[: -self._keep] if self._keep else backups:
            try:
                stale.unlink()
            except OSError:
                logger.warning("Не удалось удалить старый бэкап: %s", stale)

    async def run_forever(self) -> None:
        """Первый бэкап сразу после старта (застаём каждый деплой),
        дальше — раз в interval_hours."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                target = await loop.run_in_executor(None, self.backup_once)
                if target is not None:
                    logger.info("Бэкап БД создан", extra={"backup": str(target)})
            except Exception:
                logger.exception("Бэкап БД не удался")
            await asyncio.sleep(self._interval_seconds)


class PostgresBackupService:
    """Периодический pg_dump в сжатый custom-формат (-Fc): восстанавливается
    через pg_restore, дампит выборочно, меньше plain-SQL. Зеркалит
    SqliteBackupService по интерфейсу (enabled/run_forever) и настройкам
    (interval/keep). Дампы лежат в backup_dir — по умолчанию data/backups, в
    Docker это volume bot_data, ОТДЕЛЬНЫЙ от тома самой БД (pg_data): падение
    БД не уносит бэкапы.

    run_forever/_prune намеренно дублируют SQLite-версию, а не прячутся в общую
    базу: две простые реализации читаются легче одной универсальной."""

    def __init__(self, database_url: str, backup_dir: str, interval_hours: int, keep: int):
        self._params = postgres_params_from_url(database_url)
        self._backup_dir = Path(backup_dir)
        self._interval_seconds = interval_hours * 3600
        self._keep = keep

    @property
    def enabled(self) -> bool:
        return self._params is not None and self._interval_seconds > 0 and self._keep > 0

    @property
    def backup_dir(self) -> Path:
        return self._backup_dir

    def backup_once(self) -> Path | None:
        """Синхронный pg_dump (вызывается из executor). Возвращает путь дампа."""
        if self._params is None:
            return None
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        target = self._backup_dir / f"{self._params['dbname']}-{stamp}.dump"
        # пароль — через PGPASSWORD, не в argv: иначе виден в списке процессов
        env = {**os.environ, "PGPASSWORD": self._params["password"]}
        result = subprocess.run(
            [
                "pg_dump",
                "-Fc",
                "-h",
                self._params["host"],
                "-p",
                self._params["port"],
                "-U",
                self._params["user"],
                "-d",
                self._params["dbname"],
                "-f",
                str(target),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=_PG_DUMP_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            target.unlink(missing_ok=True)  # не оставляем битый/пустой файл
            raise RuntimeError(f"pg_dump упал ({result.returncode}): {result.stderr.strip()[:300]}")
        self._prune()
        return target

    def _prune(self) -> None:
        assert self._params is not None
        backups = sorted(self._backup_dir.glob(f"{self._params['dbname']}-*.dump"))
        for stale in backups[: -self._keep] if self._keep else backups:
            try:
                stale.unlink()
            except OSError:
                logger.warning("Не удалось удалить старый бэкап: %s", stale)

    async def run_forever(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                target = await loop.run_in_executor(None, self.backup_once)
                if target is not None:
                    logger.info("Бэкап БД создан", extra={"backup": str(target)})
            except Exception:
                logger.exception("Бэкап БД не удался")
            await asyncio.sleep(self._interval_seconds)
