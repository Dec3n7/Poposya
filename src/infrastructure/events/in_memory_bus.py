import logging
from collections import defaultdict
from collections.abc import Callable

from src.domain.events.base import DomainEvent
from src.domain.events.bus import EventHandler, IEventBus

logger = logging.getLogger(__name__)


class InMemoryEventBus(IEventBus):
    """Синхронная шина (MVP, at-most-once). Контракт ТЗ 10.3: падение одного
    подписчика логируется и не мешает остальным; хук on_handler_error
    подменяется в тестах."""

    def __init__(self) -> None:
        self._subscribers: dict[type[DomainEvent], list[EventHandler]] = defaultdict(list)
        self.on_handler_error: Callable[[DomainEvent, EventHandler, Exception], None] = (
            self._log_handler_error
        )

    def subscribe(self, event_type: type[DomainEvent], handler: EventHandler) -> None:
        self._subscribers[event_type].append(handler)

    async def publish(self, event: DomainEvent) -> None:
        for event_type, handlers in self._subscribers.items():
            if not isinstance(event, event_type):
                continue
            for handler in handlers:
                try:
                    await handler(event)
                except Exception as exc:
                    self.on_handler_error(event, handler, exc)

    @staticmethod
    def _log_handler_error(event: DomainEvent, handler: EventHandler, exc: Exception) -> None:
        logger.error(
            "Подписчик события упал",
            extra={
                "event_type": event.event_type,
                "event_id": str(event.event_id),
                "handler": getattr(handler, "__qualname__", repr(handler)),
            },
            exc_info=exc,
        )
