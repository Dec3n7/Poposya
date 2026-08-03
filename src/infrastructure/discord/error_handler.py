import logging
import uuid
from typing import cast

import discord
from discord import app_commands
from discord.ext import commands

from src.infrastructure.logging.context import LoggingContext
from src.infrastructure.persona_service import RegistryPersona

logger = logging.getLogger(__name__)

# Голос сети безопасности — каталог фраз. По умолчанию дефолты реестра
# (RegistryPersona); setup_error_handler подменяет на PersonaService бота.
# on_app_command_error остаётся модульной функцией (её ставят на bot.tree.on_error
# и импортируют тесты), поэтому персона хранится в модульном состоянии, а не в
# замыкании.
_persona = RegistryPersona()


def _friendly_message(guild_id: int, error: app_commands.AppCommandError) -> str | None:
    """Ожидаемые ошибки прав/проверок/кулдаунов — это не баги: отвечаем
    спокойно и не шумим трейсом в логах. None = ошибка неожиданная."""
    if isinstance(error, app_commands.CommandOnCooldown):
        return str(_persona.phrase(guild_id, "errors.cooldown", seconds=f"{error.retry_after:.0f}"))
    if isinstance(error, app_commands.MissingPermissions):
        return str(_persona.phrase(guild_id, "errors.missing_perms"))
    if isinstance(error, app_commands.BotMissingPermissions):
        return str(_persona.phrase(guild_id, "errors.bot_missing_perms"))
    if isinstance(error, app_commands.NoPrivateMessage):
        return str(_persona.phrase(guild_id, "errors.no_dm"))
    if isinstance(error, app_commands.CheckFailure):
        return str(_persona.phrase(guild_id, "errors.check_failure"))
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
    friendly = _friendly_message(cast(int, interaction.guild_id), error)
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
            command,
            original,
            exc_info=original,
            extra={
                "command": command,
                "user_id": getattr(interaction.user, "id", None),
                "guild_id": interaction.guild_id,
            },
        )
    await _reply(
        interaction,
        str(_persona.phrase(cast(int, interaction.guild_id), "errors.internal", code=code)),
    )


def setup_error_handler(bot: commands.Bot, persona=None) -> None:
    """Ставит глобальный обработчик и (опционально) подключает голос персоны.
    Без persona остаются дефолты реестра (RegistryPersona) — для тестов."""
    global _persona
    if persona is not None:
        _persona = persona
    bot.tree.on_error = on_app_command_error  # type: ignore[method-assign]  # идиома discord.py
