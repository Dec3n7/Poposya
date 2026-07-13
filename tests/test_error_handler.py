"""Глобальная сеть безопасности для слеш-команд: ожидаемые ошибки прав
получают спокойный ответ без трейса, неожиданные — код + трейс в логах."""
from unittest.mock import MagicMock

import pytest
from discord import app_commands

from src.infrastructure.discord.error_handler import (
    on_app_command_error,
    setup_error_handler,
)
from tests.cog_fakes import http_error, make_interaction


def _interaction(done=False):
    interaction = make_interaction()
    interaction.response.is_done = MagicMock(return_value=done)
    interaction.command = MagicMock()
    interaction.command.qualified_name = "play"
    return interaction


async def test_missing_permissions_answers_calmly_without_logging(caplog):
    interaction = _interaction()
    with caplog.at_level("ERROR"):
        await on_app_command_error(
            interaction, app_commands.MissingPermissions(["administrator"])
        )
    text, kwargs = interaction.response.send_message.call_args
    assert "прав" in text[0].lower()
    assert kwargs["ephemeral"] is True
    assert caplog.records == []  # ожидаемая ошибка — не шумим


async def test_cooldown_reports_remaining_seconds():
    interaction = _interaction()
    cooldown = app_commands.Cooldown(rate=1, per=30)
    await on_app_command_error(
        interaction, app_commands.CommandOnCooldown(cooldown, retry_after=12.0)
    )
    text, _ = interaction.response.send_message.call_args
    assert "12" in text[0]


async def test_unexpected_error_replies_with_code_and_logs_trace(caplog, monkeypatch):
    interaction = _interaction(done=False)
    monkeypatch.setattr(
        "src.infrastructure.discord.error_handler.uuid.uuid4",
        lambda: MagicMock(hex="deadbeef00"),
    )
    with caplog.at_level("ERROR"):
        # прямой AppCommandError без .original — сам себе причина
        await on_app_command_error(interaction, app_commands.AppCommandError("wrap"))
    text, kwargs = interaction.response.send_message.call_args
    assert kwargs["ephemeral"] is True
    assert "deadbeef" in text[0]  # тот же код, что ушёл в correlation_id лога
    assert len(caplog.records) == 1
    assert caplog.records[0].command == "play"


async def test_unwraps_command_invoke_error_for_trace(caplog):
    interaction = _interaction()
    original = ValueError("boom in body")
    wrapped = app_commands.CommandInvokeError(MagicMock(), original)
    with caplog.at_level("ERROR"):
        await on_app_command_error(interaction, wrapped)
    # в лог должна попасть настоящая причина, а не обёртка
    assert caplog.records[0].exc_info[1] is original


async def test_after_defer_uses_followup():
    interaction = _interaction(done=True)
    await on_app_command_error(interaction, app_commands.AppCommandError("x"))
    assert interaction.followup.send.called
    assert not interaction.response.send_message.called


async def test_reply_swallows_delivery_failure():
    interaction = _interaction()
    interaction.response.send_message.side_effect = http_error()
    # интеракция истекла — вторичная ошибка не должна всплыть наружу
    await on_app_command_error(interaction, app_commands.MissingPermissions(["x"]))


def test_setup_registers_tree_handler():
    bot = MagicMock()
    setup_error_handler(bot)
    assert bot.tree.on_error is on_app_command_error
