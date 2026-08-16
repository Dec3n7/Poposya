import asyncio
import logging
import os
import shlex
import sqlite3
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

# страховка от зависшего pg_dump: поток executor'а иначе висел бы вечно
_PG_DUMP_TIMEOUT_SECONDS = 600

# offsite-выгрузка: копия каждого свежего дампа наружу (S3/другой хост), чтобы
# отказ самого провайдера не унёс и БД, и её бэкапы вместе с боксом. Механизм —
# настраиваемая команда (rclone/scp/aws s3 …), пусто = выключено. Ошибка выгрузки
# НЕ фатальна: локальный бэкап уже сделан, offsite — лучшее-усилие поверх него.
_OFFSITE_TIMEOUT_SECONDS = 600


def offsite_argv(cmd_template: str, path: Path) -> list[str]:
    """Команда выгрузки → argv с подстановкой пути дампа. `{path}` в шаблоне
    заменяется на путь; если плейсхолдера нет — путь добавляется последним
    аргументом (тогда `rclone copy remote:bucket` соберётся с дампом сам)."""
    if "{path}" in cmd_template:
        return [part.replace("{path}", str(path)) for part in shlex.split(cmd_template)]
    return [*shlex.split(cmd_template), str(path)]


def command_offsite_uploader(cmd_template: str) -> Callable[[Path], None] | None:
    """Строит выгрузчик из команды; пустой шаблон → None (offsite выключен).
    Команду задаёт оператор в .env — доверенный вход, поэтому без shell (argv
    через shlex, чтобы имя файла-дампа не могло стать инъекцией)."""
    if not cmd_template.strip():
        return None

    def upload(path: Path) -> None:
        result = subprocess.run(
            offsite_argv(cmd_template, path),
            capture_output=True,
            text=True,
            timeout=_OFFSITE_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"offsite-команда упала ({result.returncode}): {result.stderr.strip()[:300]}"
            )

    return upload


def ship_offsite(offsite: Callable[[Path], None] | None, path: Path) -> None:
    """Выгрузка дампа наружу — НЕ фатально при ошибке: локальная копия уже есть,
    и провал offsite не должен рвать цикл бэкапов. Синхронна (зовётся из
    executor, как и сам pg_dump)."""
    if offsite is None:
        return
    try:
        offsite(path)
        logger.info("Бэкап выгружен offsite", extra={"backup": str(path)})
    except Exception:
        logger.exception("Offsite-выгрузка бэкапа не удалась (локальная копия цела)")


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


def make_backup_service(
    database_url: str, backup_dir: str, interval_hours: int, keep: int, offsite_cmd: str = ""
):
    """Правильный сервис бэкапа под тип БД; None — тип неизвестен (диагностика
    об этом предупредит отдельно). `offsite_cmd` (пусто = выкл) включает выгрузку
    каждого свежего дампа наружу поверх локальной копии."""
    offsite = command_offsite_uploader(offsite_cmd)
    if database_url.startswith("sqlite"):
        return SqliteBackupService(database_url, interval_hours, keep, offsite)
    if database_url.startswith("postgresql"):
        return PostgresBackupService(database_url, backup_dir, interval_hours, keep, offsite)
    return None


class SqliteBackupService:
    """Периодическая копия базы штатным online backup API SQLite — безопасно
    на живой БД, в отличие от копирования файла (риск порванной страницы).
    Бэкапы лежат в <каталог БД>/backups (в Docker это volume bot_data),
    старые ротируются, хранится keep последних."""

    def __init__(
        self,
        database_url: str,
        interval_hours: int,
        keep: int,
        offsite: Callable[[Path], None] | None = None,
    ):
        self._db_path = sqlite_path_from_url(database_url)
        self._interval_seconds = interval_hours * 3600
        self._keep = keep
        self._offsite = offsite

    @property
    def enabled(self) -> bool:
        return self._db_path is not None and self._interval_seconds > 0 and self._keep > 0

    @property
    def backup_dir(self) -> Path:
        # зовётся только при enabled / после None-проверки в backup_once
        return cast(Path, self._db_path).parent / "backups"

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
        backups = sorted(self.backup_dir.glob(f"{cast(Path, self._db_path).stem}-*.db"))
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
                    await loop.run_in_executor(None, ship_offsite, self._offsite, target)
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

    def __init__(
        self,
        database_url: str,
        backup_dir: str,
        interval_hours: int,
        keep: int,
        offsite: Callable[[Path], None] | None = None,
    ):
        self._params = postgres_params_from_url(database_url)
        self._backup_dir = Path(backup_dir)
        self._interval_seconds = interval_hours * 3600
        self._keep = keep
        self._offsite = offsite

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
                    await loop.run_in_executor(None, ship_offsite, self._offsite, target)
            except Exception:
                logger.exception("Бэкап БД не удался")
            await asyncio.sleep(self._interval_seconds)
