from dataclasses import dataclass

from src.domain.events.base import DomainEvent
from src.infrastructure.events.in_memory_bus import InMemoryEventBus


@dataclass(frozen=True, kw_only=True)
class SampleEvent(DomainEvent):
    event_type: str = "test.sample"


async def test_event_bus_error_isolation():
    """Контракт ТЗ 10.3: падение одного подписчика не мешает остальным."""
    bus = InMemoryEventBus()
    errors: list[Exception] = []
    success_handler_called = False

    async def failing_handler(event):
        raise ValueError("Handler failed")

    async def success_handler(event):
        nonlocal success_handler_called
        success_handler_called = True

    bus.on_handler_error = lambda event, handler, exc: errors.append(exc)

    bus.subscribe(SampleEvent, failing_handler)
    bus.subscribe(SampleEvent, success_handler)

    await bus.publish(SampleEvent(aggregate_id="test"))

    assert success_handler_called
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)


async def test_subscriber_receives_only_its_event_type():
    bus = InMemoryEventBus()
    received: list[DomainEvent] = []

    @dataclass(frozen=True, kw_only=True)
    class OtherEvent(DomainEvent):
        event_type: str = "test.other"

    async def handler(event):
        received.append(event)

    bus.subscribe(SampleEvent, handler)
    await bus.publish(OtherEvent(aggregate_id="x"))
    assert received == []

    event = SampleEvent(aggregate_id="y")
    await bus.publish(event)
    assert received == [event]
