from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from src.domain.events.base import DomainEvent

EventHandler = Callable[[DomainEvent], Awaitable[None]]


class IEventBus(ABC):
    @abstractmethod
    async def publish(self, event: DomainEvent) -> None: ...

    @abstractmethod
    def subscribe(self, event_type: type[DomainEvent], handler: EventHandler) -> None: ...
