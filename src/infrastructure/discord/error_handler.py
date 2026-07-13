import logging
import uuid

import discord
from discord import app_commands
from discord.ext import commands

from src.infrastructure.logging.context import LoggingContext

logger = logging.getLogger(__name__)


def _friendly_message(error: app_commands.AppCommandError) -> str | None:
    """Ожидаемые ошибки прав/проверок/кулдаунов — это не баги: отвечаем
    спокойно и не шумим трейсом в логах. None = ошибка неожиданная."""
    if isinstance(error, app_commands.CommandOnCooldown):
        return f"Слишком часто. Попробуй через {error.retry_after:.0f} с."
    if isinstance(error, app_commands.MissingPermissions):
        return "У тебя нет прав на эту команду."
    if isinstance(error, app_commands.BotMissingPermissions):
        return "Мне не хватает прав в этом канале, чтобы выполнить команду."
    if isinstance(error, app_commands.NoPrivateMessage):
        return "Эта команда работает только на сервере."
    if isinstance(error, app_commands.CheckFailure):
        return "Команда сейчас недоступна."
    return None


async def _reply(interaction: discord.Interaction, text: str) -> None:
    """Отвечаем эфемерно и с оглядкой на состояние интеракции: после defer()
    ответ уже «отдан», поэтому идём через followup."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        # интеракция могла истечь (>15 мин) или уже быть закрыта — не даём
        # вторичной ошибке всплыть поверх исходной
        logger.debug("Не удалось доставить пользователю сообщение об ошибке команды")


async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    """Единая сеть безопасности для всех слеш-команд. Коги валидируют свои
    ожидаемые случаи сами; сюда долетает то, что они не поймали."""
    friendly = _friendly_message(error)
    if friendly is not None:
        await _reply(interaction, friendly)
        return

    # discord оборачивает исключения из тела команды в CommandInvokeError —
    # разворачиваем до настоящей причины для внятного трейса
    original = getattr(error, "original", error)
    command = interaction.command.qualified_name if interaction.command else "?"
    # короткий код: один и тот же в логе (correlation_id) и в ответе юзеру,
    # чтобы по нему можно было найти трейс
    code = uuid.uuid4().hex[:8]
    with LoggingContext.correlation_id(code):
        logger.error(
            "Необработанная ошибка команды /%s: %s",
            command, original,
            exc_info=original,
            extra={
                "command": command,
                "user_id": getattr(interaction.user, "id", None),
                "guild_id": interaction.guild_id,
            },
        )
    await _reply(
        interaction,
        "Ой, что-то сломалось на моей стороне. Я записала ошибку — "
        f"назови код `{code}`, если повторится.",
    )


def setup_error_handler(bot: commands.Bot) -> None:
    bot.tree.on_error = on_app_command_error
