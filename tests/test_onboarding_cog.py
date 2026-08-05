"""OnboardingCog: выбор канала (system → фолбэк → нет), отправка приветствия,
условная ссылка на панель, устойчивость к отсутствию канала и сбою отправки."""

from unittest.mock import AsyncMock, MagicMock

import discord

from src.config import Settings
from src.infrastructure.discord.cogs.onboarding import OnboardingCog

from .cog_fakes import http_error


def make_settings(**over):
    return Settings(_env_file=None, discord_token="t", **over)


def make_cog(**over):
    return OnboardingCog(MagicMock(), make_settings(**over))


def _channel(send=True, view=True):
    ch = MagicMock()
    ch.permissions_for.return_value = MagicMock(send_messages=send, view_channel=view)
    ch.send = AsyncMock()
    return ch


def _guild(system=None, text_channels=(), gid=10):
    guild = MagicMock()
    guild.id = gid
    guild.me = object()  # не None
    guild.system_channel = system
    guild.text_channels = list(text_channels)
    return guild


# --- выбор канала ----------------------------------------------------------


def test_target_prefers_system_channel():
    cog = make_cog()
    system = _channel(send=True)
    assert cog._target_channel(_guild(system=system, text_channels=[_channel()])) is system


def test_target_falls_back_when_no_system():
    cog = make_cog()
    fallback = _channel(send=True)
    assert cog._target_channel(_guild(system=None, text_channels=[fallback])) is fallback


def test_target_skips_unpostable_system():
    cog = make_cog()
    fallback = _channel(send=True)
    guild = _guild(system=_channel(send=False), text_channels=[fallback])
    assert cog._target_channel(guild) is fallback


def test_target_none_when_nowhere_to_post():
    cog = make_cog()
    guild = _guild(system=_channel(send=False), text_channels=[_channel(send=False)])
    assert cog._target_channel(guild) is None


# --- отправка приветствия ---------------------------------------------------


async def test_sends_embed_to_target():
    cog = make_cog()
    system = _channel(send=True)
    await cog.on_guild_join(_guild(system=system))
    system.send.assert_awaited_once()
    kwargs = system.send.await_args.kwargs
    assert isinstance(kwargs["embed"], discord.Embed)
    assert kwargs["allowed_mentions"].everyone is False  # без пингов


async def test_no_channel_no_crash():
    cog = make_cog()
    guild = _guild(system=_channel(send=False), text_channels=[])
    await cog.on_guild_join(guild)  # не должно бросить


async def test_send_failure_swallowed():
    cog = make_cog()
    system = _channel(send=True)
    system.send = AsyncMock(side_effect=http_error())
    await cog.on_guild_join(_guild(system=system))  # не должно бросить


# --- содержимое эмбеда ------------------------------------------------------


def _field(embed, needle):
    return next(f for f in embed.fields if needle in f.name)


async def test_embed_has_greeting_and_setup():
    cog = make_cog()
    embed = await cog._build_embed(_guild())
    assert embed.description  # приветствие + «чем полезна»
    setup = _field(embed, "настроить").value
    assert "/config" in setup
    assert "/forgetme" in _field(embed, "Приватность").value


async def test_panel_link_only_when_public_url_set():
    cog = make_cog(web_public_url="https://panel.example")
    with_url = await cog._build_embed(_guild())
    assert "https://panel.example" in _field(with_url, "настроить").value

    without = await make_cog()._build_embed(_guild())
    assert "Панель управления" not in _field(without, "настроить").value
