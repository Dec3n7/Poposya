"""Общие фикстуры: движок БД, фабрика сессий и фабрика UnitOfWork с
in-memory шиной событий.

По умолчанию — SQLite в tmp_path (быстро, изолированно, ничего не нужно).
Переменная TEST_DATABASE_URL гоняет тот же набор против другой БД:

    TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/test pytest

Это не роскошь: бот живёт на PostgreSQL, а SQLite прощает то, чего Postgres не
простит (наивные даты, boolean-дефолты, конкурентные записи). Проверять на
SQLite то, что работает на Postgres — значит проверять не то.
"""

import os
import random

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.infrastructure.db.models.base import Base
from src.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from src.infrastructure.events.in_memory_bus import InMemoryEventBus


@pytest.fixture(autouse=True)
def _deterministic_random():
    """Профилактика: глобальный `random` сеется фиксированно перед каждым тестом,
    чтобы немоканые random-гейты не зависели от порядка сбора/энтропии между
    прогонами. Уптайм-независимость кулдаунов держит уже сам прод (`.get(k,
    float("-inf"))` вместо 0.0), так что monotonic здесь НЕ подменяем — иначе
    тесты перестали бы ловить регресс этого класса на свежем CI-раннере."""
    random.seed(0)


def database_url(tmp_path) -> str:
    return os.environ.get("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(database_url(tmp_path))
    async with engine.begin() as conn:
        # SQLite получает свой файл на каждый тест, а внешняя БД одна на весь
        # прогон — поэтому схему сносим и поднимаем заново перед каждым тестом
        await conn.run_sync(Base.metadata.drop_all)
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
