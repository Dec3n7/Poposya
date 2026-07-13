import asyncio
import logging
from collections import deque
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from src.config import Settings

_FLUSH_INTERVAL = 5
_MAX_QUEUE = 500
_MAX_CHUNKS_PER_FLUSH = 4

# логгеры, которые нельзя ретранслировать в Discord — отправка сообщения
# сама порождает их записи (бесконечная петля)
_EXCLUDED_PREFIXES = ("discord", "aiohttp", "websockets", __name__)


class _NoiseFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith(_EXCLUDED_PREFIXES)


class DiscordLogHandler(logging.Handler):
    """Складывает записи в очередь; отправкой занимается фоновая задача кога.
    emit() синхронный и не трогает сеть — блокировок event loop нет."""

    def __init__(self, level: int):
        super().__init__(level)
        self.queue: deque[str] = deque(maxlen=_MAX_QUEUE)
        self.addFilter(_NoiseFilter())
        self.setFormatter(
            logging.Formatter("`%(asctime)s` **%(levelname)s** `%(name)s` %(message)s", "%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.queue.append(self.format(record)[:1800])
        except Exception:
            self.handleError(record)


class LogRelayCog(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings):
        self.bot = bot
        self.channel_id = settings.discord_log_channel
        level_name = settings.discord_log_level.upper()
        self.handler = DiscordLogHandler(getattr(logging, level_name, logging.WARNING))
        self._flush_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        logging.getLogger().addHandler(self.handler)
        self._flush_task = asyncio.create_task(self._flush_loop())

    def cog_unload(self) -> None:
        logging.getLogger().removeHandler(self.handler)
        if self._flush_task is not None:
            self._flush_task.cancel()

    async def _flush_loop(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            await asyncio.sleep(_FLUSH_INTERVAL)
            if not self.channel_id or not self.handler.queue:
                continue
            channel = self.bot.get_channel(self.channel_id)
            if channel is None:
                continue
            lines: list[str] = []
            while self.handler.queue:
                lines.append(self.handler.queue.popleft())
            chunks: list[str] = []
            current = ""
            for line in lines:
                if len(current) + len(line) + 1 > 1900:
                    chunks.append(current)
                    current = ""
                current += line + "\n"
            if current:
                chunks.append(current)
            dropped = len(chunks) - _MAX_CHUNKS_PER_FLUSH
            for chunk in chunks[:_MAX_CHUNKS_PER_FLUSH]:
                try:
                    await channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    return  # канал недоступен — не спамим попытками в этом цикле
            if dropped > 0:
                try:
                    await channel.send(f"*…опущено ещё {dropped} блоков логов*")
                except discord.HTTPException:
                    pass

    @app_commands.command(
        name="botlog", description="Логи бота в канал: уровень и куда писать"
    )
    @app_commands.describe(
        level="Минимальный уровень (OFF = выключить)",
        channel="Канал для логов (по умолчанию — текущий выбор)",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def botlog(
        self,
        interaction: discord.Interaction,
        level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "OFF"],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if channel is not None:
            self.channel_id = channel.id
        if level == "OFF":
            self.handler.setLevel(logging.CRITICAL + 1)
            await interaction.response.send_message(
                "Логи в Discord выключены.", ephemeral=True
            )
            return
        self.handler.setLevel(getattr(logging, level))
        target = self.bot.get_channel(self.channel_id)
        where = target.mention if target else "канал не задан — укажи параметр channel"
        await interaction.response.send_message(
            f"Логи: уровень **{level}**, канал: {where}.\n"
            "-# До рестарта; постоянные значения — DISCORD_LOG_LEVEL и "
            "DISCORD_LOG_CHANNEL в .env",
            ephemeral=True,
        )
