"""LogRelayCog: обработчик-очередь, шумовой фильтр, команда /botlog."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.infrastructure.discord.cogs.log_relay import (
    DiscordLogHandler,
    LogRelayCog,
    _NoiseFilter,
)
from tests.cog_fakes import make_interaction


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
