from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    # Postgres в проде: pre_ping отсеивает соединения, умершие после рестарта БД
    # или сетевого сбоя (иначе первый запрос на протухшем коннекте падает),
    # recycle закрывает их до серверного idle-таймаута. Пул с запасом под два
    # процесса (бот + панель), у каждого свой движок; потолок Postgres по
    # умолчанию 100 коннектов — суммарно укладываемся.
    if database_url.startswith("postgresql"):
        return create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=10,
            max_overflow=20,
        )
    # SQLite — локальный файл, пула соединений в этом смысле нет, параметры не нужны
    return create_async_engine(database_url)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
