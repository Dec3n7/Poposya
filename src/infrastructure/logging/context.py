import contextvars
from contextlib import contextmanager
from uuid import UUID

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)


class LoggingContext:
    """contextvars, а не classvar-словарь: параллельные asyncio-задачи
    получают изолированную копию контекста (ТЗ 9.1)."""

    @staticmethod
    @contextmanager
    def correlation_id(value: UUID | str):
        token = _correlation_id.set(str(value))
        try:
            yield
        finally:
            _correlation_id.reset(token)

    @staticmethod
    def get() -> str | None:
        return _correlation_id.get()
