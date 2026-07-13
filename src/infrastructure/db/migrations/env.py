import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import Settings
from src.infrastructure.db.models.base import Base
import src.infrastructure.db.models.activity  # noqa: F401 — регистрация моделей
import src.infrastructure.db.models.cinema  # noqa: F401
import src.infrastructure.db.models.finds  # noqa: F401
import src.infrastructure.db.models.guild  # noqa: F401
import src.infrastructure.db.models.moderation  # noqa: F401
import src.infrastructure.db.models.music  # noqa: F401
import src.infrastructure.db.models.outbox  # noqa: F401
import src.infrastructure.db.models.relationship  # noqa: F401

target_metadata = Base.metadata


def _database_url() -> str:
    return Settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,  # обязательный batch mode для SQLite (ТЗ 5.3)
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
