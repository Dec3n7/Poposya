from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    # Postgres в проде: pre_ping отсеивает соединения, умершие после рестарта БД
    # или сетевого сбоя (иначе первый запрос на протухшем коннекте падает),
    # recycle закрывает их до серверного idle-таймаута.
    #
    # Размер пула (A4, docs/plans/scale-300-guilds.md): 5 постоянных + 10 burst
    # на процесс. Бот и панель — по одному asyncio-циклу каждый: одновременных
    # обращений к БД в норме единицы (outbox-диспетчер, листенер команд, коги на
    # событие), 15 с запасом покрывают пик. Итого 15 × 2 процесса = 30 при
    # потолке Postgres 100 — остаётся вдвое-втрое воздуха под psql/миграции/рост.
    # Прежние 10+20 (=60) были оверпровижном: пул резервирует бэкенды Postgres
    # (память на коннект), не выбирая их нагрузкой.
    if database_url.startswith("postgresql"):
        return create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=5,
            max_overflow=10,
        )
    # SQLite — локальный файл, пула соединений в этом смысле нет, параметры не нужны
    return create_async_engine(database_url)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
