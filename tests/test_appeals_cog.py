"""AppealsCog: кнопка-в-ЛС (build_button_view), разбор из панели
(resolve_from_panel: снятие/уведомление/идемпотентность) и гейт прав."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

import src.infrastructure.discord.cogs.appeals as mod
from src.application.appeals.use_cases import ResolveResult
from src.infrastructure.discord.cogs.appeals import (
    ACTION_BAN,
    ACTION_MUTE,
    AppealsCog,
    _can_resolve,
)


def make_cog(*, resolve_result=None, appeals_channel=123, enabled=True):
    bot = MagicMock()
    appeals = SimpleNamespace(
        resolve=SimpleNamespace(execute=AsyncMock(return_value=resolve_result))
    )
    settings = SimpleNamespace(appeals_enabled=enabled, appeals_channel=appeals_channel)
    persona = SimpleNamespace(phrase=lambda gid, key, **kw: "Обжаловать")
    cog = AppealsCog(bot, appeals, settings, guild_settings=None, persona=persona)
    return cog, bot


def _member(**perms) -> MagicMock:
    m = MagicMock()
    m.guild_permissions = discord.Permissions(**perms)
    return m


# --- гейт прав ---


def test_can_resolve_ban_needs_ban_members():
    assert _can_resolve(_member(ban_members=True), ACTION_BAN) is True
    assert _can_resolve(_member(moderate_members=True), ACTION_BAN) is False


def test_can_resolve_mute_needs_moderate_members():
    assert _can_resolve(_member(moderate_members=True), ACTION_MUTE) is True
    assert _can_resolve(_member(ban_members=True), ACTION_MUTE) is False


def test_can_resolve_admin_can_everything():
    assert _can_resolve(_member(administrator=True), ACTION_BAN) is True
    assert _can_resolve(_member(administrator=True), ACTION_MUTE) is True


# --- кнопка в ЛС ---


def test_button_view_present_when_enabled():
    cog, _bot = make_cog(enabled=True, appeals_channel=123)
    view = cog.build_button_view(10, "ban")
    assert view is not None and len(view.children) == 1


def test_button_view_none_when_disabled():
    cog, _bot = make_cog(enabled=False, appeals_channel=123)
    assert cog.build_button_view(10, "ban") is None


def test_button_view_none_without_channel():
    cog, _bot = make_cog(enabled=True, appeals_channel=0)
    assert cog.build_button_view(10, "ban") is None


# --- разбор из панели ---


async def test_resolve_from_panel_approve_lifts_and_notifies(monkeypatch):
    lift = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(mod, "_lift_punishment", lift)
    monkeypatch.setattr(mod, "_notify_appellant", notify)
    appeal = SimpleNamespace(action="ban", user_id=5, guild_id=10)
    cog, _bot = make_cog(resolve_result=ResolveResult(ok=True, appeal=appeal, approved=True))

    msg = await cog.resolve_from_panel(MagicMock(), 1, True, 99)

    lift.assert_awaited_once()
    notify.assert_awaited_once()
    assert "принята" in msg.lower()


async def test_resolve_from_panel_reject_does_not_lift(monkeypatch):
    lift = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(mod, "_lift_punishment", lift)
    monkeypatch.setattr(mod, "_notify_appellant", notify)
    appeal = SimpleNamespace(action="ban", user_id=5, guild_id=10)
    cog, _bot = make_cog(resolve_result=ResolveResult(ok=True, appeal=appeal, approved=False))

    msg = await cog.resolve_from_panel(MagicMock(), 1, False, 99)

    lift.assert_not_awaited()
    notify.assert_awaited_once()
    assert "отклон" in msg.lower()


async def test_resolve_from_panel_already(monkeypatch):
    monkeypatch.setattr(mod, "_lift_punishment", AsyncMock())
    monkeypatch.setattr(mod, "_notify_appellant", AsyncMock())
    cog, _bot = make_cog(resolve_result=ResolveResult(ok=False, error="already"))
    msg = await cog.resolve_from_panel(MagicMock(), 1, True, 99)
    assert "уже" in msg.lower()
