"""Общие фикстуры: SQLite-движок в файле tmp_path, фабрика сессий и
фабрика UnitOfWork с in-memory шиной событий."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.infrastructure.db.models.base import Base
from src.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from src.infrastructure.events.in_memory_bus import InMemoryEventBus


@pytest.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def event_bus():
    return InMemoryEventBus()


@pytest.fixture
def uow_factory(session_factory, event_bus):
    def factory():
        return SqlAlchemyUnitOfWork(session_factory, event_bus)

    return factory
