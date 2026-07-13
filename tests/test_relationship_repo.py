from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.infrastructure.db.models.base import Base
from src.infrastructure.db.repositories.relationship import SqlAlchemyRelationshipRepository
from src.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from src.infrastructure.events.in_memory_bus import InMemoryEventBus


@pytest.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_roundtrip(session_factory):
    now = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
    async with session_factory() as session:
        repo = SqlAlchemyRelationshipRepository(session)
        profile = await repo.get_or_create(1, 10)
        profile.points = 55
        profile.user_notes = "любит Sekiro"
        profile.last_dialog_at = now
        await repo.save(profile)
        await session.commit()

    async with session_factory() as session:
        repo = SqlAlchemyRelationshipRepository(session)
        loaded = await repo.get(1, 10)
        assert loaded is not None
        assert loaded.points == 55
        assert loaded.user_notes == "любит Sekiro"
        assert loaded.last_dialog_at == now  # tz восстановлен как UTC


async def test_exclusive_holder_lookup(session_factory):
    async with session_factory() as session:
        repo = SqlAlchemyRelationshipRepository(session)
        first = await repo.get_or_create(1, 10)
        first.points = 400
        first.is_exclusive = True
        await repo.save(first)
        second = await repo.get_or_create(2, 10)
        second.points = 300
        await repo.save(second)
        await session.commit()

    async with session_factory() as session:
        repo = SqlAlchemyRelationshipRepository(session)
        holder = await repo.get_exclusive_holder(10)
        assert holder is not None and holder.user_id == 1
        # identity map: повторный запрос возвращает тот же объект
        again = await repo.get(1, 10)
        assert again is holder


async def test_uow_publishes_events_only_after_commit(session_factory):
    from dataclasses import dataclass

    from src.domain.events.base import DomainEvent

    @dataclass(frozen=True, kw_only=True)
    class Ping(DomainEvent):
        event_type: str = "test.ping"

    bus = InMemoryEventBus()
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe(Ping, handler)

    # без commit события не публикуются
    async with SqlAlchemyUnitOfWork(session_factory, bus) as uow:
        profile = await uow.relationships.get_or_create(1, 10)
        profile.points = 5
        await uow.relationships.save(profile)
        uow.add_event(Ping(aggregate_id="x"))
    assert received == []

    # с commit — публикуются, и данные сохранены
    async with SqlAlchemyUnitOfWork(session_factory, bus) as uow:
        profile = await uow.relationships.get_or_create(1, 10)
        profile.points = 7
        await uow.relationships.save(profile)
        uow.add_event(Ping(aggregate_id="x"))
        await uow.commit()
    assert len(received) == 1

    async with SqlAlchemyUnitOfWork(session_factory, bus) as uow:
        profile = await uow.relationships.get(1, 10)
        assert profile is not None and profile.points == 7
