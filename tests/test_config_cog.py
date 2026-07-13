"""ConfigCog: /config list/show/set/reset и автодополнение ключей.
Сервис настроек — реальный GuildSettingsService поверх SQLite."""

import pytest

from src.config import Settings
from src.infrastructure.discord.cogs.config import ConfigCog
from src.infrastructure.guild_settings import GuildSettingsService
from tests.cog_fakes import make_interaction


def make_settings(**over):
    return Settings(_env_file=None, discord_token="t", **over)


@pytest.fixture
async def service(session_factory):
    svc = GuildSettingsService(make_settings(), session_factory)
    await svc.load_all()
    return svc


def make_cog(service):
    from unittest.mock import MagicMock

    return ConfigCog(MagicMock(), service)


async def test_list_shows_all_keys(service):
    cog = make_cog(service)
    interaction = make_interaction()
    await type(cog).config_list.callback(cog, interaction)
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert "warn_threshold" in embed.description
    assert "cinema_rating_hours" in embed.description


async def test_show_unknown_key(service):
    cog = make_cog(service)
    interaction = make_interaction()
    await type(cog).config_show.callback(cog, interaction, "нет_такого")
    assert "Нет такой" in interaction.response.send_message.await_args.args[0]


async def test_show_reports_default_and_override(service):
    cog = make_cog(service)
    await service.set(10, "warn_threshold", "5")
    interaction = make_interaction(guild_id=10)
    await type(cog).config_show.callback(cog, interaction, "warn_threshold")
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert "переопределено" in embed.description
    assert "Диапазон" in embed.description  # int-настройка показывает диапазон


async def test_set_valid(service):
    cog = make_cog(service)
    interaction = make_interaction(guild_id=10)
    await type(cog).config_set.callback(cog, interaction, "spam_limit", "8")
    assert "spam_limit" in interaction.response.send_message.await_args.args[0]
    assert service.current(10, "spam_limit") == 8


async def test_set_invalid_value(service):
    cog = make_cog(service)
    interaction = make_interaction(guild_id=10)
    await type(cog).config_set.callback(cog, interaction, "warn_threshold", "999")
    assert "Не приняла" in interaction.response.send_message.await_args.args[0]
    assert service.is_override(10, "warn_threshold") is False


async def test_set_channel_key(service):
    cog = make_cog(service)
    interaction = make_interaction(guild_id=10)
    await type(cog).config_set.callback(cog, interaction, "cinema_forum_channel", "<#555>")
    assert service.current(10, "cinema_forum_channel") == 555
    assert "<#555>" in interaction.response.send_message.await_args.args[0]


async def test_reset(service):
    cog = make_cog(service)
    await service.set(10, "lonely_hours", "6")
    interaction = make_interaction(guild_id=10)
    await type(cog).config_reset.callback(cog, interaction, "lonely_hours")
    assert "сброшено" in interaction.response.send_message.await_args.args[0]
    assert service.is_override(10, "lonely_hours") is False


async def test_reset_when_not_set(service):
    cog = make_cog(service)
    interaction = make_interaction(guild_id=10)
    await type(cog).config_reset.callback(cog, interaction, "lonely_hours")
    assert "и так на дефолте" in interaction.response.send_message.await_args.args[0]


async def test_key_autocomplete_filters(service):
    cog = make_cog(service)
    interaction = make_interaction()
    choices = await type(cog)._key_autocomplete(cog, interaction, "spam")
    values = [c.value for c in choices]
    assert "spam_limit" in values and "spam_window" in values
    assert "warn_threshold" not in values
