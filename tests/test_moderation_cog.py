"""ModerationCog: вызываем callback'и слеш-команд напрямую с фейковыми
Interaction/Member/container — проверяем ветвление, без живого Discord."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.application.moderation.use_cases import WarnResult
from src.domain.moderation.entities import TempBan, Warn
from src.infrastructure.discord.cogs.moderation import ModerationCog

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def forbidden():
    return discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "no perms")


def not_found():
    return discord.NotFound(MagicMock(status=404, reason="Not Found"), "gone")


def make_settings(**over):
    base = dict(
        spam_window=10, spam_limit=5, spam_mute_minutes=2,
        warn_threshold=3, warn_mute_minutes=120, log_channel=0,
    )
    base.update(over)
    return SimpleNamespace(**base)


def make_container():
    c = SimpleNamespace()
    c.warn_user = SimpleNamespace(execute=AsyncMock())
    c.get_warns = SimpleNamespace(execute=AsyncMock(return_value=[]))
    c.clear_warns = SimpleNamespace(execute=AsyncMock(return_value=0))
    c.temp_ban = SimpleNamespace(execute=AsyncMock(return_value=NOW))
    c.remove_ban = SimpleNamespace(execute=AsyncMock(return_value=True))
    c.list_bans = SimpleNamespace(execute=AsyncMock(return_value=[]))
    c.pop_expired_bans = SimpleNamespace(execute=AsyncMock(return_value=[]))
    return c


class Named:
    """Объект с осмысленным str() — Interaction.user попадает в тексты логов."""

    def __init__(self, uid, name):
        self.id = uid
        self._name = name

    def __str__(self):
        return self._name


def make_interaction(guild_id=10):
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.user = Named(99, "Mod#1")
    interaction.channel = MagicMock()
    interaction.channel.name = "general"
    interaction.channel.send = AsyncMock()
    interaction.channel.purge = AsyncMock(return_value=[1, 2, 3])
    interaction.channel.edit = AsyncMock()
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def make_member(uid=1, bot=False):
    member = MagicMock()
    member.id = uid
    member.bot = bot
    member.mention = f"<@{uid}>"
    member.display_name = f"User{uid}"
    member.timeout = AsyncMock()
    member.guild = MagicMock()
    member.guild.id = 10
    return member


def make_cog(container=None, settings=None):
    bot = MagicMock()
    return ModerationCog(bot, container or make_container(), settings or make_settings())


# --- /say -------------------------------------------------------------------

async def test_say_sends_to_channel():
    cog = make_cog()
    interaction = make_interaction()
    interaction.channel.send = AsyncMock()
    await type(cog).say.callback(cog, interaction, "привет", None)
    interaction.channel.send.assert_awaited_once()
    interaction.response.send_message.assert_awaited_once()


async def test_say_forbidden():
    cog = make_cog()
    interaction = make_interaction()
    target = MagicMock()
    target.mention = "#c"
    target.send = AsyncMock(side_effect=forbidden())
    await type(cog).say.callback(cog, interaction, "hi", target)
    # сообщил об ошибке прав
    args = interaction.response.send_message.await_args
    assert "Нет прав" in args.args[0]


# --- /warn ------------------------------------------------------------------

async def test_warn_bot_rejected():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).warn.callback(cog, interaction, make_member(bot=True), "spam")
    assert "Ботов не варним" in interaction.response.send_message.await_args.args[0]


async def test_warn_normal():
    container = make_container()
    container.warn_user.execute.return_value = WarnResult(count=1, threshold=3, mute_triggered=False)
    cog = make_cog(container)
    interaction = make_interaction()
    user = make_member()
    await type(cog).warn.callback(cog, interaction, user, "флуд")
    msg = interaction.response.send_message.await_args.args[0]
    assert "варн 1/3" in msg
    user.timeout.assert_not_called()


async def test_warn_triggers_mute():
    container = make_container()
    container.warn_user.execute.return_value = WarnResult(count=3, threshold=3, mute_triggered=True)
    cog = make_cog(container)
    interaction = make_interaction()
    user = make_member()
    await type(cog).warn.callback(cog, interaction, user, "перебор")
    user.timeout.assert_awaited_once()  # мут по достижении порога
    assert "мут" in interaction.response.send_message.await_args.args[0]


# --- /warnings, /clearwarns -------------------------------------------------

async def test_warnings_empty():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).warnings.callback(cog, interaction, make_member())
    assert "нет активных" in interaction.response.send_message.await_args.args[0]


async def test_warnings_list():
    container = make_container()
    container.get_warns.execute.return_value = [
        Warn(guild_id=10, user_id=1, moderator_id=99, reason="a", created_at=NOW),
    ]
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).warnings.callback(cog, interaction, make_member())
    kwargs = interaction.response.send_message.await_args.kwargs
    assert "embed" in kwargs


async def test_clearwarns_reports_count():
    container = make_container()
    container.clear_warns.execute.return_value = 2
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).clearwarns.callback(cog, interaction, make_member())
    assert "2" in interaction.response.send_message.await_args.args[0]


# --- /mute, /unmute ---------------------------------------------------------

async def test_mute_success():
    cog = make_cog()
    interaction = make_interaction()
    user = make_member()
    await type(cog).mute.callback(cog, interaction, user, 30, "шум")
    user.timeout.assert_awaited_once()
    assert "замучен на 30" in interaction.response.send_message.await_args.args[0]


async def test_mute_forbidden_reports():
    cog = make_cog()
    interaction = make_interaction()
    user = make_member()
    user.timeout = AsyncMock(side_effect=forbidden())
    await type(cog).mute.callback(cog, interaction, user, 30, "шум")
    assert "Не получилось" in interaction.response.send_message.await_args.args[0]


async def test_unmute_success():
    cog = make_cog()
    interaction = make_interaction()
    user = make_member()
    await type(cog).unmute.callback(cog, interaction, user)
    user.timeout.assert_awaited_once_with(None, reason="Снято Mod#1")


async def test_unmute_failure():
    cog = make_cog()
    interaction = make_interaction()
    user = make_member()
    user.timeout = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500, reason="x"), "e"))
    await type(cog).unmute.callback(cog, interaction, user)
    assert "Не получилось" in interaction.response.send_message.await_args.args[0]


# --- /tempban, /unban, /bans -----------------------------------------------

async def test_tempban_success():
    container = make_container()
    container.temp_ban.execute.return_value = NOW
    cog = make_cog(container)
    interaction = make_interaction()
    interaction.guild.ban = AsyncMock()
    user = make_member()
    await type(cog).tempban.callback(cog, interaction, user, 60, "рейд")
    interaction.guild.ban.assert_awaited_once()
    container.temp_ban.execute.assert_awaited_once()
    interaction.followup.send.assert_awaited()


async def test_tempban_forbidden():
    cog = make_cog()
    interaction = make_interaction()
    interaction.guild.ban = AsyncMock(side_effect=forbidden())
    await type(cog).tempban.callback(cog, interaction, make_member(), 60, "рейд")
    assert "Нет права Ban" in interaction.followup.send.await_args.args[0]


async def test_unban_bad_id():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).unban.callback(cog, interaction, "not-a-number")
    assert "Это не ID" in interaction.response.send_message.await_args.args[0]


async def test_unban_not_found():
    cog = make_cog()
    interaction = make_interaction()
    interaction.guild.unban = AsyncMock(side_effect=not_found())
    await type(cog).unban.callback(cog, interaction, "555")
    assert "не в бане" in interaction.response.send_message.await_args.args[0]


async def test_unban_success():
    container = make_container()
    cog = make_cog(container)
    interaction = make_interaction()
    interaction.guild.unban = AsyncMock()
    await type(cog).unban.callback(cog, interaction, "555")
    container.remove_ban.execute.assert_awaited_once_with(555, 10)
    assert "разбанен" in interaction.response.send_message.await_args.args[0]


async def test_bans_empty():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).bans.callback(cog, interaction)
    assert "нет" in interaction.response.send_message.await_args.args[0].lower()


async def test_bans_list():
    container = make_container()
    container.list_bans.execute.return_value = [
        TempBan(guild_id=10, user_id=1, moderator_id=99, reason="r", expires_at=NOW),
    ]
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).bans.callback(cog, interaction)
    assert "embed" in interaction.response.send_message.await_args.kwargs


# --- /clear, /slowmode ------------------------------------------------------

async def test_clear_purges():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).clear.callback(cog, interaction, 3)
    interaction.channel.purge.assert_awaited_once()
    assert "Удалено сообщений: 3" in interaction.followup.send.await_args.args[0]


async def test_slowmode_on_and_off():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).slowmode.callback(cog, interaction, 10)
    interaction.channel.edit.assert_awaited_with(slowmode_delay=10)
    assert "10 c" in interaction.response.send_message.await_args.args[0]

    interaction2 = make_interaction()
    await type(cog).slowmode.callback(cog, interaction2, 0)
    assert "выключен" in interaction2.response.send_message.await_args.args[0]


# --- /rage ------------------------------------------------------------------

async def test_rage_not_in_voice():
    cog = make_cog()
    interaction = make_interaction()
    user = make_member()
    user.voice = None
    await type(cog).rage.callback(cog, interaction, user)
    assert "не в голосовом" in interaction.response.send_message.await_args.args[0]


async def test_rage_moves_and_kicks():
    cog = make_cog()
    interaction = make_interaction()
    current = MagicMock()
    other = MagicMock()
    interaction.guild.voice_channels = [current, other]
    interaction.guild.kick = AsyncMock()
    user = make_member()
    user.voice = SimpleNamespace(channel=current)
    user.move_to = AsyncMock()
    await type(cog).rage.callback(cog, interaction, user)
    user.move_to.assert_awaited()
    interaction.guild.kick.assert_awaited_once()


# --- helpers: _timeout / _log ----------------------------------------------

async def test_log_skipped_without_channel():
    cog = make_cog(settings=make_settings(log_channel=0))
    guild = MagicMock()
    await cog._log(guild, "text")
    guild.get_channel.assert_not_called()


async def test_log_sends_to_channel():
    cog = make_cog(settings=make_settings(log_channel=500))
    guild = MagicMock()
    channel = MagicMock()
    channel.send = AsyncMock()
    guild.get_channel.return_value = channel
    await cog._log(guild, "событие")
    channel.send.assert_awaited_once()


async def test_timeout_returns_false_on_forbidden():
    cog = make_cog()
    member = make_member()
    member.timeout = AsyncMock(side_effect=forbidden())
    assert await cog._timeout(member, 10, "reason") is False
