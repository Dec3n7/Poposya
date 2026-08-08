"""Перенос данных SQLite → PostgreSQL.

Бот переносим между бэкендами (весь тест-набор идёт и на SQLite, и на Postgres),
но данные сами не переезжают: смена `DATABASE_URL` даёт пустую базу — об этом
честно предупреждает README. Этот скрипт закрывает ту «отдельную задачу»:
копирует содержимое файловой SQLite в уже поднятую Postgres — таблица за
таблицей, в порядке зависимостей внешних ключей, с починкой sequence'ов после
массовой вставки.

**Схему не создаёт.** Её накатывает сам бот на старте (`alembic upgrade head`
при `AUTO_MIGRATE`), и тогда же в целевой БД появляется `alembic_version` с
верной ревизией. Скрипт лишь наливает данные в готовые пустые таблицы — иначе
он затёр бы учёт ревизий Alembic и следующий старт бота сломался бы на «таблица
уже существует».

Порядок:
    1. Поднять Postgres и дать боту стартовать один раз (создаст схему) ИЛИ
       выполнить `alembic upgrade head` против Postgres.
    2. Остановить бота (чтобы он не писал в целевую БД во время переноса).
    3. python -m src.infrastructure.db.migrate_sqlite_to_pg \
           --source ./poposya.db \
           --dest "postgresql+asyncpg://poposya:PASS@localhost:5432/poposya"
    4. Запустить бота.

Копирование идёт через SQLAlchemy Core по метадате моделей: типы (boolean 0/1 →
true/false, JSON, даты) интерпретируются по описанию колонок, а не переносятся
как сырые байты SQLite.
"""

import argparse
import asyncio
import importlib
import pkgutil
import sys

from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from src.infrastructure.db.models.base import Base


class MigrationError(RuntimeError):
    """Перенос остановлен до записи: несовпадение ревизий, нет схемы, целевая
    БД не пуста. Всегда до первой вставки — целевая БД не тронута."""


def discover_models() -> None:
    """Импортирует все модули `models/*`, чтобы `Base.metadata` знала все таблицы.

    `models/__init__.py` пуст, а `migrations/env.py` перечисляет лишь часть
    модулей — полагаться на них для полноты нельзя. Обходим пакет и импортируем
    каждый подмодуль: новые модели подхватятся сами, без правки этого файла.
    """
    import src.infrastructure.db.models as models_pkg

    for info in pkgutil.iter_modules(models_pkg.__path__):
        if info.name != "base":
            importlib.import_module(f"{models_pkg.__name__}.{info.name}")


def normalize_source(source: str) -> str:
    """Принимаем и путь к файлу, и готовый URL — путь удобнее для человека."""
    if "://" in source:
        return source
    return f"sqlite+aiosqlite:///{source}"


async def _alembic_revision(conn: AsyncConnection) -> str | None:
    """Ревизия Alembic или None, если таблицы нет (схему поднимали через
    create_all, а не миграциями — так делают тесты)."""
    try:
        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
    except Exception:
        return None
    row = result.first()
    return str(row[0]) if row else None


async def _table_count(conn: AsyncConnection, table) -> int:
    result = await conn.execute(select(func.count()).select_from(table))
    return int(result.scalar_one())


async def _reset_sequences(conn: AsyncConnection, tables) -> None:
    """Подтягивает sequence'ы Postgres к максимуму после массовой вставки.

    Строки перенесены с явными id, а sequence при этом не двигается — и первый
    же insert приложения столкнулся бы с занятым id. Имя sequence спрашиваем у
    самого Postgres (`pg_get_serial_sequence` вернёт NULL для не-serial колонок),
    так что подстройка касается ровно тех колонок, у которых он есть, без
    угадывания. is_called=false на пустой таблице отдаёт первый nextval = 1.
    """
    for table in tables:
        for col in table.columns:
            seq = (
                await conn.execute(
                    text("SELECT pg_get_serial_sequence(:t, :c)"),
                    {"t": table.name, "c": col.name},
                )
            ).scalar()
            if seq is None:
                continue
            await conn.execute(
                text(
                    f"SELECT setval(:seq, "
                    f'COALESCE((SELECT MAX("{col.name}") FROM "{table.name}"), 1), '
                    f'(SELECT MAX("{col.name}") FROM "{table.name}") IS NOT NULL)'
                ),
                {"seq": seq},
            )


async def migrate(
    source_url: str,
    dest_url: str,
    *,
    force: bool = False,
    batch_size: int = 1000,
) -> dict[str, int]:
    """Копирует все таблицы из SQLite в Postgres. Возвращает {таблица: строк}.

    Всё копирование — в одной транзакции целевой БД: любой сбой откатывает
    перенос целиком, полу-заполненной базы не остаётся.
    """
    discover_models()
    tables = list(Base.metadata.sorted_tables)  # родители раньше детей (FK-порядок)

    src = create_async_engine(source_url)
    dst = create_async_engine(dest_url)
    try:
        # 1. схема на месте? (наличие таблиц, а не alembic_version: create_all
        #    тоже создаёт схему, но без учётной таблицы)
        async with dst.connect() as dc:
            existing = set(await dc.run_sync(lambda c: inspect(c).get_table_names()))
        missing = [t.name for t in tables if t.name not in existing]
        if missing:
            raise MigrationError(
                "в целевой БД нет части таблиц: "
                f"{', '.join(sorted(missing)[:5])}{'…' if len(missing) > 5 else ''}. "
                "Сначала подними схему: старт бота с AUTO_MIGRATE или `alembic upgrade head`."
            )

        # 2. ревизии совпадают? (сверяем, только если обе БД учтены Alembic)
        async with src.connect() as sc, dst.connect() as dc:
            src_rev = await _alembic_revision(sc)
            dst_rev = await _alembic_revision(dc)
        if src_rev is not None and dst_rev is not None and src_rev != dst_rev:
            raise MigrationError(
                f"ревизии схемы расходятся: SQLite={src_rev}, Postgres={dst_rev}. "
                "Приведи обе к одной (`alembic upgrade head`) и повтори."
            )

        # 3. целевые таблицы пусты? (защита от вставки в живую базу)
        async with dst.connect() as dc:
            nonempty = [t.name for t in tables if await _table_count(dc, t) > 0]
        if nonempty and not force:
            raise MigrationError(
                f"целевые таблицы не пусты: {', '.join(nonempty[:5])}"
                f"{'…' if len(nonempty) > 5 else ''}. "
                "Перенос рассчитан на чистую базу; --force чтобы дописать поверх."
            )

        # 4. копия в одной транзакции целевой БД
        report: dict[str, int] = {}
        async with src.connect() as sc, dst.begin() as dc:
            for table in tables:
                copied = 0
                result = await sc.stream(table.select())
                async for partition in result.partitions(batch_size):
                    rows = [dict(row._mapping) for row in partition]
                    if rows:
                        await dc.execute(table.insert(), rows)
                        copied += len(rows)
                report[table.name] = copied
            # 5. sequences — только Postgres (у SQLite их нет)
            if dc.dialect.name == "postgresql":
                await _reset_sequences(dc, tables)
        return report
    finally:
        await src.dispose()
        await dst.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="migrate_sqlite_to_pg",
        description="Перенос данных бота из SQLite в PostgreSQL (схему не создаёт).",
    )
    parser.add_argument(
        "--source",
        required=True,
        help="путь к файлу SQLite (или полный URL sqlite+aiosqlite://…)",
    )
    parser.add_argument(
        "--dest",
        required=True,
        help="URL целевой Postgres: postgresql+asyncpg://user:pass@host:5432/db",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="дописать даже в непустые таблицы (по умолчанию перенос требует чистой базы)",
    )
    parser.add_argument("--batch-size", type=int, default=1000, help="строк на вставку")
    args = parser.parse_args(argv)

    source_url = normalize_source(args.source)
    if not args.dest.startswith("postgresql"):
        print("--dest должен быть postgresql+asyncpg://…", file=sys.stderr)
        return 2

    try:
        report = asyncio.run(
            migrate(source_url, args.dest, force=args.force, batch_size=args.batch_size)
        )
    except MigrationError as exc:
        print(f"миграция прервана: {exc}", file=sys.stderr)
        return 1

    print("Перенос завершён. Строк по таблицам:")
    for name, count in report.items():
        if count:
            print(f"  {name}: {count}")
    print(f"Итого: {sum(report.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
