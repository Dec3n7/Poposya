"""Число затронутых строк DML-запроса (INSERT/UPDATE/DELETE).

`AsyncSession.execute()` статически типизирован как `Result[Any]`, у которого
атрибута `.rowcount` нет. Для DML драйвер на деле возвращает `CursorResult`,
где `.rowcount` есть всегда. Приведение здесь — единственная честная
альтернатива `# type: ignore[attr-defined]`, размазанному по каждому
репозиторию: причина задокументирована в одном месте, а call-site читается
как обычный вызов.
"""

from typing import Any, cast

from sqlalchemy import CursorResult
from sqlalchemy.engine import Result


def rows_affected(result: Result[Any]) -> int:
    return cast("CursorResult[Any]", result).rowcount
