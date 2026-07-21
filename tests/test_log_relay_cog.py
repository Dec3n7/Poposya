"""LogRelayCog: обработчик-очередь, шумовой фильтр, команда /botlog,
cog_load/unload и фоновый _flush_loop (доставка накопленных логов чанками)."""

import asyncio
import contextlib
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.infrastructure.discord.cogs import log_relay as log_relay_module
from src.infrastructure.discord.cogs.log_relay import (
    DiscordLogHandler,
    LogRelayCog,
    _NoiseFilter,
)
from tests.cog_fakes import http_error, make_interaction


def _record(name="app.module", level=logging.WARNING, msg="боевое"):
    return logging.LogRecord(name, level, __file__, 1, msg, None, None)


def test_noise_filter_excludes_internal_loggers():
    f = _NoiseFilter()
    assert f.filter(_record("app.module")) is True
    assert f.filter(_record("discord.gateway")) is False
    assert f.filter(_record("aiohttp.access")) is False


def test_handler_queues_formatted_record():
    handler = DiscordLogHandler(logging.WARNING)
    handler.emit(_record(msg="что-то сломалось"))
    assert len(handler.queue) == 1
    assert "что-то сломалось" in handler.queue[0]
    assert "WARNING" in handler.queue[0]


def test_handler_filter_drops_discord_records():
    handler = DiscordLogHandler(logging.WARNING)
    # emit проходит через фильтры хендлера только при handle(); тут проверяем сам фильтр
    rec = _record("discord.client")
    assert handler.filter(rec) is False


def test_handler_truncates_long_message():
    handler = DiscordLogHandler(logging.DEBUG)
    handler.emit(_record(msg="x" * 5000))
    assert len(handler.queue[0]) <= 1800


def make_cog(discord_log_channel=0, discord_log_level="WARNING"):
    bot = MagicMock()
    settings = SimpleNamespace(
        discord_log_channel=discord_log_channel,
        discord_log_level=discord_log_level,
    )
    return LogRelayCog(bot, settings)


def test_cog_init_sets_handler_level():
    cog = make_cog(discord_log_level="ERROR")
    assert cog.handler.level == logging.ERROR


def test_cog_init_defaults_bad_level_to_warning():
    cog = make_cog(discord_log_level="NONSENSE")
    assert cog.handler.level == logging.WARNING


async def test_botlog_off_disables():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).botlog.callback(cog, interaction, "OFF", None)
    assert cog.handler.level > logging.CRITICAL
    assert "выключены" in interaction.response.send_message.await_args.args[0]


async def test_botlog_sets_level_and_channel():
    cog = make_cog()
    interaction = make_interaction()
    channel = MagicMock()
    channel.id = 777
    channel.mention = "#logs"
    cog.bot.get_channel.return_value = channel
    await type(cog).botlog.callback(cog, interaction, "INFO", channel)
    assert cog.channel_id == 777
    assert cog.handler.level == logging.INFO
    assert "INFO" in interaction.response.send_message.await_args.args[0]


async def test_botlog_no_channel_hint():
    cog = make_cog()
    interaction = make_interaction()
    cog.bot.get_channel.return_value = None
    await type(cog).botlog.callback(cog, interaction, "DEBUG", None)
    assert "канал не задан" in interaction.response.send_message.await_args.args[0]


def test_handler_emit_falls_back_to_handle_error_on_format_failure(monkeypatch):
    handler = DiscordLogHandler(logging.WARNING)
    monkeypatch.setattr(handler, "format", MagicMock(side_effect=RuntimeError("boom")))
    handled = []
    monkeypatch.setattr(handler, "handleError", lambda record: handled.append(record))
    rec = _record()
    handler.emit(rec)
    assert handled == [rec]


# --- cog_load / cog_unload ---------------------------------------------------


async def test_cog_load_registers_handler_and_starts_flush_task(monkeypatch):
    cog = make_cog()
    cog.bot.wait_until_ready = AsyncMock()
    added = []
    monkeypatch.setattr(logging.getLogger(), "addHandler", lambda h: added.append(h))
    await cog.cog_load()
    assert added == [cog.handler]
    assert cog._flush_task is not None
    cog._flush_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cog._flush_task


def test_cog_unload_removes_handler_and_cancels_flush_task():
    cog = make_cog()
    real_handler = DiscordLogHandler(logging.WARNING)
    cog.handler = real_handler
    task = MagicMock()
    cog._flush_task = task
    logging.getLogger().addHandler(real_handler)
    try:
        cog.cog_unload()
    finally:
        with contextlib.suppress(ValueError):
            logging.getLogger().removeHandler(real_handler)
    task.cancel.assert_called_once()
    assert real_handler not in logging.getLogger().handlers


def test_cog_unload_without_flush_task_does_not_crash():
    cog = make_cog()
    assert cog._flush_task is None
    cog.cog_unload()  # не падает — задачи ещё не было


# --- _flush_loop --------------------------------------------------------------


async def _drive_flush_loop(monkeypatch, cog, iterations):
    """Прогоняет _flush_loop ровно `iterations` раз (по одному на await sleep),
    затем сама себя останавливает — без реального ожидания и без второй задачи."""
    calls = 0

    async def fake_sleep(_):
        nonlocal calls
        calls += 1
        if calls > iterations:
            raise asyncio.CancelledError()

    monkeypatch.setattr(log_relay_module.asyncio, "sleep", fake_sleep)
    cog.bot.wait_until_ready = AsyncMock()
    with contextlib.suppress(asyncio.CancelledError):
        await cog._flush_loop()


async def test_flush_loop_skips_when_no_channel_id(monkeypatch):
    cog = make_cog(discord_log_channel=0)
    cog.handler.queue.append("line")
    await _drive_flush_loop(monkeypatch, cog, iterations=1)
    cog.bot.get_channel.assert_not_called()
    assert len(cog.handler.queue) == 1


async def test_flush_loop_skips_when_queue_empty(monkeypatch):
    cog = make_cog(discord_log_channel=123)
    await _drive_flush_loop(monkeypatch, cog, iterations=1)
    cog.bot.get_channel.assert_not_called()


async def test_flush_loop_skips_when_channel_missing(monkeypatch):
    cog = make_cog(discord_log_channel=123)
    cog.handler.queue.append("line")
    cog.bot.get_channel.return_value = None
    await _drive_flush_loop(monkeypatch, cog, iterations=1)
    cog.bot.get_channel.assert_called_once_with(123)
    assert len(cog.handler.queue) == 1  # канал недоступен — очередь не тронута


async def test_flush_loop_sends_chunks_and_reports_dropped(monkeypatch):
    cog = make_cog(discord_log_channel=123)
    for _ in range(5):
        cog.handler.queue.append("x" * 1000)  # каждая строка — свой чанк
    channel = MagicMock()
    channel.send = AsyncMock()
    cog.bot.get_channel.return_value = channel
    await _drive_flush_loop(monkeypatch, cog, iterations=1)
    assert channel.send.await_count == 5  # 4 чанка (лимит) + сообщение о пропуске
    assert "опущено ещё 1" in channel.send.await_args_list[-1].args[0]
    assert len(cog.handler.queue) == 0  # очередь снята атомарно


async def test_flush_loop_stops_on_chunk_http_exception(monkeypatch):
    cog = make_cog(discord_log_channel=123)
    cog.handler.queue.append("боевая строка")
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=http_error())
    cog.bot.get_channel.return_value = channel
    # iterations большой — цикл должен вернуться сам на первой итерации, не дожидаясь отмены
    await _drive_flush_loop(monkeypatch, cog, iterations=5)
    channel.send.assert_awaited_once()


async def test_flush_loop_dropped_notice_http_exception_is_ignored(monkeypatch):
    cog = make_cog(discord_log_channel=123)
    for _ in range(5):
        cog.handler.queue.append("x" * 1000)
    channel = MagicMock()
    # первые 4 отправки ок, «опущено» — падает и молча проглатывается
    channel.send = AsyncMock(side_effect=[None, None, None, None, http_error()])
    cog.bot.get_channel.return_value = channel
    await _drive_flush_loop(monkeypatch, cog, iterations=1)
    assert channel.send.await_count == 5
