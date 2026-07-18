"""Сквозной путь Postgres LISTEN/NOTIFY: запись настройки одним процессом
инвалидирует in-memory кэш другого.

Только PostgreSQL: механизм на NOTIFY, на SQLite его нет (один писатель).
Проверяем ровно то, что даёт веб-панели право писать настройки в обход бота:
панель пишет -> бот перечитывает кэш этой гильдии сам, без рестарта.
"""

import asyncio
import contextlib
import os
import time

import pytest

from src.config import Settings
from src.infrastructure.guild_settings import GuildSettingsService
from src.infrastructure.settings_listener import make_settings_listener

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="LISTEN/NOTIFY есть только в Postgres; на SQLite второго писателя нет",
)


def make_settings(**over):
    return Settings(_env_file=None, discord_token="t", **over)


async def _wait_until(predicate, timeout: float = 4.0, step: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(step)
    return predicate()


async def test_notify_invalidates_bot_cache(session_factory):
    """«Панель» пишет warn_threshold, «бот» с отдельным кэшем видит новое
    значение сам — по NOTIFY, а не по рестарту."""
    url = os.environ["TEST_DATABASE_URL"]
    bot = GuildSettingsService(make_settings(), session_factory)
    panel = GuildSettingsService(make_settings(), session_factory)
    await bot.load_all()

    listener = make_settings_listener(url, bot)
    assert listener is not None
    task = asyncio.create_task(listener.run_forever())
    try:
        # дать листенеру подключиться и сделать LISTEN (его connect-time load_all
        # отработает по пустой БД, поэтому дальше значение приходит только по NOTIFY)
        await asyncio.sleep(1.0)
        await panel.set(10, "warn_threshold", "9")  # -> pg_notify в той же транзакции
        got = await _wait_until(lambda: bot.current(10, "warn_threshold") == 9)
        assert got, "бот не перечитал настройку по NOTIFY"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_notify_invalidates_on_reset(session_factory):
    """reset панели тоже долетает: переопределение снимается у бота по NOTIFY."""
    url = os.environ["TEST_DATABASE_URL"]
    bot = GuildSettingsService(make_settings(), session_factory)
    panel = GuildSettingsService(make_settings(), session_factory)
    await panel.set(10, "lonely_hours", "6")
    await bot.load_all()  # бот стартует, уже зная про переопределение
    assert bot.current(10, "lonely_hours") == 6

    listener = make_settings_listener(url, bot)
    task = asyncio.create_task(listener.run_forever())
    try:
        await asyncio.sleep(1.0)
        await panel.reset(10, "lonely_hours")  # -> pg_notify
        got = await _wait_until(
            lambda: bot.current(10, "lonely_hours") == make_settings().lonely_hours
        )
        assert got, "бот не увидел снятие настройки по NOTIFY"
        assert bot.is_override(10, "lonely_hours") is False
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
