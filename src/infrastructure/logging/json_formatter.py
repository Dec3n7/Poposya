import json
import logging
import re
from datetime import UTC, datetime

from src.infrastructure.logging.context import LoggingContext

# Стандартные атрибуты LogRecord — всё остальное считаем extra-полями
_STANDARD_ATTRS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "message",
    "asctime",
}

# Правило ТЗ 9.1: секрет, попавший в Settings, не должен утечь через логи
_SECRET_KEY_RE = re.compile(r"(_api_key|_token|password|database_url|^token)$", re.IGNORECASE)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        correlation_id = LoggingContext.get()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key.startswith("_"):
                continue
            payload[key] = "***" if _SECRET_KEY_RE.search(key) else value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO", fmt: str = "json", log_file: str = "") -> None:
    """Консоль — на LOG_LEVEL; файл (если задан) — всегда DEBUG с ротацией."""
    plain = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setLevel(level.upper())
    console.setFormatter(JsonFormatter() if fmt == "json" else plain)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(console)
    root.setLevel(logging.DEBUG if log_file else level.upper())

    if log_file:
        from logging.handlers import RotatingFileHandler
        from pathlib import Path

        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(plain)
        root.addHandler(file_handler)

    # шумные логгеры внешних библиотек
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    # Запрос к health-серверу — единственное, что попадает в access-лог: бот
    # обслуживает по HTTP только /health, /ready и /health/full. За неделю это
    # дало 85% всех строк лога (17692 из 20767), а с внешним мониторингом,
    # опрашивающим раз в 10с, доля выросла бы до ~95%. Полезного там нет:
    # ошибки бот пишет сам, а неудачу зонда фиксирует тот, кто зондирует.
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
