import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def sqlite_path_from_url(database_url: str) -> Path | None:
    """sqlite+aiosqlite:///./poposya.db -> ./poposya.db; не-SQLite -> None."""
    if not database_url.startswith("sqlite"):
        return None
    _, sep, tail = database_url.partition("///")
    if not sep or not tail:
        return None
    return Path(tail)


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
        return (
            self._db_path is not None
            and self._interval_seconds > 0
            and self._keep > 0
        )

    @property
    def backup_dir(self) -> Path:
        return self._db_path.parent / "backups"

    def backup_once(self) -> Path | None:
        """Синхронный бэкап (вызывается из executor). Возвращает путь копии."""
        if self._db_path is None or not self._db_path.exists():
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
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
