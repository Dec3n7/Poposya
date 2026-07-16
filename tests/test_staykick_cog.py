"""StayKickCog: ЛС новичку при входе, колбэки кнопок, фоновый цикл."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from src.domain.staykick.entities import PendingKick
from src.infrastructure.discord.cogs.staykick import StayKickCog

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def make_cog(enabled=True):
    container = MagicMock()
    container.cancel_kick.execute = AsyncMock(return_value=True)
    container.schedule_kick.execute = AsyncMock(return_value=NOW)
    container.pop_due_kicks.execute = AsyncMock(return_value=[])
    container.due_reminders.execute = AsyncMock(return_value=[])
    settings = SimpleNamespace(
        staykick_enabled=enabled, staykick_hours=12, staykick_remind_before_minutes=60
    )
    cog = StayKickCog(MagicMock(), container, settings)
    return cog, container


def _member(is_bot=False, guild_id=10, user_id=1):
    m = MagicMock()
    m.bot = is_bot
    m.guild = SimpleNamespace(id=guild_id)
    m.id = user_id
    m.send = AsyncMock()
    return m


def _interaction(user_id=1):
    i = MagicMock()
    i.user = SimpleNamespace(id=user_id)
    i.response.edit_message = AsyncMock()
    return i


async def test_join_dms_when_enabled():
    cog, _ = make_cog(enabled=True)
    member = _member()
    await cog.on_member_join(member)
    member.send.assert_awaited_once()
    assert "view" in member.send.await_args.kwargs  # с кнопками


async def test_join_skips_when_disabled():
    cog, _ = make_cog(enabled=False)
    member = _member()
    await cog.on_member_join(member)
    member.send.assert_not_awaited()


async def test_join_skips_bots():
    cog, _ = make_cog(enabled=True)
    member = _member(is_bot=True)
    await cog.on_member_join(member)
    member.send.assert_not_awaited()


async def test_join_dm_forbidden_swallowed():
    cog, _ = make_cog(enabled=True)
    member = _member()
    member.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no dm"))
    await cog.on_member_join(member)  # не падает — по дефолту остаётся


async def test_on_stay_cancels_and_closes():
    cog, container = make_cog()
    interaction = _interaction(user_id=7)
    await cog.on_stay(interaction, 10)
    container.cancel_kick.execute.assert_awaited_once_with(10, 7)
    interaction.response.edit_message.assert_awaited_once()


async def test_on_leave_schedules_and_closes():
    cog, container = make_cog()
    interaction = _interaction(user_id=7)
    await cog.on_leave(interaction, 10)
    args = container.schedule_kick.execute.await_args.args
    assert args[0] == 10 and args[1] == 7 and args[3] == 12  # guild, user, hours
    interaction.response.edit_message.assert_awaited_once()


async def test_tick_reminds_and_kicks():
    cog, container = make_cog()
    pk = PendingKick(10, 1, NOW, NOW, created_at=NOW)
    container.due_reminders.execute = AsyncMock(return_value=[pk])
    container.pop_due_kicks.execute = AsyncMock(return_value=[pk])
    member = _member()
    guild = MagicMock()
    guild.get_member.return_value = member
    guild.kick = AsyncMock()
    member.guild = guild
    cog.bot.get_guild.return_value = guild
    await cog._tick(NOW)
    assert member.send.await_count == 2  # напоминание + прощание
    guild.kick.assert_awaited_once()


async def test_tick_skips_kick_if_member_left():
    cog, container = make_cog()
    pk = PendingKick(10, 1, NOW, NOW, created_at=NOW)
    container.pop_due_kicks.execute = AsyncMock(return_value=[pk])
    guild = MagicMock()
    guild.get_member.return_value = None  # уже ушёл
    guild.kick = AsyncMock()
    cog.bot.get_guild.return_value = guild
    await cog._tick(NOW)
    guild.kick.assert_not_awaited()
