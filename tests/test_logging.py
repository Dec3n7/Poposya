"""Тесты JSON-логгера и correlation-context: маскирование секретов,
extra-поля, exception, изоляция correlation_id по asyncio-задачам."""

import asyncio
import json
import logging

from src.infrastructure.logging.context import LoggingContext
from src.infrastructure.logging.json_formatter import JsonFormatter, setup_logging


def _record(msg="hi", level=logging.INFO, **extra):
    rec = logging.LogRecord("test.logger", level, __file__, 10, msg, None, None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_basic_payload_shape():
    out = json.loads(JsonFormatter().format(_record("привет")))
    assert out["level"] == "INFO"
    assert out["logger"] == "test.logger"
    assert out["message"] == "привет"
    assert "ts" in out and out["ts"].endswith("+00:00")


def test_extra_fields_passed_through():
    out = json.loads(JsonFormatter().format(_record(user_id=42, guild="x")))
    assert out["user_id"] == 42
    assert out["guild"] == "x"


def test_secret_keys_masked():
    out = json.loads(
        JsonFormatter().format(
            _record(
                groq_api_key="sk-secret",
                discord_token="tok",
                password="p",
                database_url="postgres://x",
                token="raw",
                user_id=1,
            )
        )
    )
    assert out["groq_api_key"] == "***"
    assert out["discord_token"] == "***"
    assert out["password"] == "***"
    assert out["database_url"] == "***"
    assert out["token"] == "***"
    assert out["user_id"] == 1  # не секрет — виден


def test_underscore_and_standard_attrs_skipped():
    out = json.loads(JsonFormatter().format(_record(_private="hidden")))
    assert "_private" not in out
    # стандартные атрибуты LogRecord тоже не протекают
    assert "pathname" not in out and "lineno" not in out


def test_message_with_args_formatted():
    rec = logging.LogRecord("l", logging.INFO, __file__, 1, "hello %s #%d", ("world", 7), None)
    out = json.loads(JsonFormatter().format(rec))
    assert out["message"] == "hello world #7"


def test_exception_included():
    try:
        raise ValueError("bad")
    except ValueError:
        import sys

        rec = logging.LogRecord("l", logging.ERROR, __file__, 1, "boom", None, sys.exc_info())
    out = json.loads(JsonFormatter().format(rec))
    assert "exception" in out
    assert "ValueError: bad" in out["exception"]


def test_correlation_id_added_when_set():
    with LoggingContext.correlation_id("abc-123"):
        out = json.loads(JsonFormatter().format(_record()))
        assert out["correlation_id"] == "abc-123"
    # вне контекста — поля нет
    out2 = json.loads(JsonFormatter().format(_record()))
    assert "correlation_id" not in out2


def test_correlation_id_accepts_uuid():
    from uuid import uuid4

    u = uuid4()
    with LoggingContext.correlation_id(u):
        assert LoggingContext.get() == str(u)


async def test_correlation_id_isolated_per_task():
    seen = {}

    async def worker(name):
        with LoggingContext.correlation_id(name):
            await asyncio.sleep(0)
            seen[name] = LoggingContext.get()

    await asyncio.gather(worker("A"), worker("B"))
    assert seen == {"A": "A", "B": "B"}
    assert LoggingContext.get() is None


def test_setup_logging_json_handler():
    root = logging.getLogger()
    old = root.handlers[:]
    try:
        setup_logging(level="DEBUG", fmt="json")
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
        assert logging.getLogger("discord").level == logging.WARNING
    finally:
        root.handlers[:] = old


def test_setup_logging_plain_and_file(tmp_path):
    root = logging.getLogger()
    old = root.handlers[:]
    old_level = root.level
    try:
        log_file = tmp_path / "logs" / "app.log"
        setup_logging(level="INFO", fmt="plain", log_file=str(log_file))
        # консоль + файловый обработчик
        assert len(root.handlers) == 2
        assert root.level == logging.DEBUG  # при файле корень всегда DEBUG
        assert log_file.parent.exists()
    finally:
        for h in root.handlers:
            if h not in old:
                h.close()
        root.handlers[:] = old
        root.setLevel(old_level)
