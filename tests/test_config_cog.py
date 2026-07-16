"""ConfigCog: /config list/show/reset + типизированные channel/toggle/number
и автодополнение ключей. Сервис — реальный GuildSettingsService поверх SQLite."""

from unittest.mock import MagicMock

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
    return ConfigCog(MagicMock(), service)


def fake_channel(cid=555):
    ch = MagicMock()
    ch.id = cid
    return ch


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


async def test_number_sets_int(service):
    cog = make_cog(service)
    interaction = make_interaction(guild_id=10)
    await type(cog).config_number.callback(cog, interaction, "spam_limit", 8.0)
    assert "spam_limit" in interaction.response.send_message.await_args.args[0]
    assert service.current(10, "spam_limit") == 8


async def test_number_sets_float(service):
    cog = make_cog(service)
    interaction = make_interaction(guild_id=10)
    await type(cog).config_number.callback(cog, interaction, "ai_event_comment_chance", 0.3)
    assert service.current(10, "ai_event_comment_chance") == 0.3


async def test_number_rejects_out_of_range(service):
    cog = make_cog(service)
    interaction = make_interaction(guild_id=10)
    await type(cog).config_number.callback(cog, interaction, "warn_threshold", 999.0)
    assert "Не приняла" in interaction.response.send_message.await_args.args[0]
    assert service.is_override(10, "warn_threshold") is False


async def test_number_rejects_fraction_for_int_key(service):
    cog = make_cog(service)
    interaction = make_interaction(guild_id=10)
    await type(cog).config_number.callback(cog, interaction, "spam_limit", 8.5)
    assert "Не приняла" in interaction.response.send_message.await_args.args[0]
    assert service.is_override(10, "spam_limit") is False


async def test_number_rejects_wrong_kind(service):
    cog = make_cog(service)
    interaction = make_interaction(guild_id=10)
    # channel-ключ через числовую команду не проходит
    await type(cog).config_number.callback(cog, interaction, "cinema_forum_channel", 5.0)
    assert "числовой" in interaction.response.send_message.await_args.args[0]


async def test_toggle_sets_bool(service):
    cog = make_cog(service)
    interaction = make_interaction(guild_id=10)
    await type(cog).config_toggle.callback(cog, interaction, "music_karaoke_ansi", True)
    assert service.current(10, "music_karaoke_ansi") == 1
    assert "вкл" in interaction.response.send_message.await_args.args[0]


async def test_channel_sets_and_clears(service):
    cog = make_cog(service)
    interaction = make_interaction(guild_id=10)
    await type(cog).config_channel.callback(
        cog, interaction, "cinema_forum_channel", fake_channel(555)
    )
    assert service.current(10, "cinema_forum_channel") == 555
    assert "<#555>" in interaction.response.send_message.await_args.args[0]
    # без канала — выключить (0)
    interaction2 = make_interaction(guild_id=10)
    await type(cog).config_channel.callback(cog, interaction2, "cinema_forum_channel", None)
    assert service.current(10, "cinema_forum_channel") == 0


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


async def test_apply_roles_valid(service):
    cog = make_cog(service)
    interaction = make_interaction(guild_id=10)
    await cog._apply_roles(interaction, "50, 150", "a\nb\nc")
    assert service.resolved(10).points_policy().thresholds == (50, 150)
    assert service.resolved(10).relationship_role_names == ["a", "b", "c"]
    assert "Роли обновлены" in interaction.response.send_message.await_args.args[0]


async def test_apply_roles_bad_thresholds(service):
    cog = make_cog(service)
    interaction = make_interaction(guild_id=10)
    await cog._apply_roles(interaction, "50, абв", "a\nb\nc")
    assert "целые числа" in interaction.response.send_message.await_args.args[0]
    assert service.is_override(10, "relationship_role_thresholds") is False


async def test_apply_roles_invariant_rejected(service):
    cog = make_cog(service)
    interaction = make_interaction(guild_id=10)
    await cog._apply_roles(interaction, "50, 150", "a\nb")  # 2 имени на 2 порога — мало
    assert "Не приняла" in interaction.response.send_message.await_args.args[0]
    assert service.is_override(10, "relationship_role_names") is False


async def test_apply_limits_valid(service):
    cog = make_cog(service)
    interaction = make_interaction(guild_id=10)
    await cog._apply_limits(interaction, "1: 3\n2: 7\n3: 9")
    assert service.resolved(10).ai_rate_limits_by_level == {1: 3, 2: 7, 3: 9}


async def test_apply_limits_bad_format(service):
    cog = make_cog(service)
    interaction = make_interaction(guild_id=10)
    await cog._apply_limits(interaction, "мусор без двоеточия")
    assert "Не приняла" in interaction.response.send_message.await_args.args[0]


async def test_roles_reset(service):
    cog = make_cog(service)
    await service.set_many(
        10, {"relationship_role_thresholds": [50, 150], "relationship_role_names": ["a", "b", "c"]}
    )
    interaction = make_interaction(guild_id=10)
    await type(cog).config_roles.callback(cog, interaction, reset=True)
    assert service.is_override(10, "relationship_role_thresholds") is False
    assert service.is_override(10, "relationship_role_names") is False
    assert "сброшены" in interaction.response.send_message.await_args.args[0]


async def test_typed_autocomplete_filters_by_kind(service):
    cog = make_cog(service)
    interaction = make_interaction()
    channel_keys = [c.value for c in await type(cog)._channel_key_ac(cog, interaction, "")]
    assert "cinema_forum_channel" in channel_keys
    assert "warn_threshold" not in channel_keys  # не канал
    bool_keys = [c.value for c in await type(cog)._bool_key_ac(cog, interaction, "")]
    assert "music_karaoke_ansi" in bool_keys
    assert "warn_threshold" not in bool_keys  # не bool
    number_keys = [c.value for c in await type(cog)._number_key_ac(cog, interaction, "")]
    assert "warn_threshold" in number_keys
    assert "music_karaoke_ansi" not in number_keys  # не число
