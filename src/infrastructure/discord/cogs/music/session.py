"""Состояние музыкальной сессии одной гильдии.

Раньше ког держал девять параллельных словарей guild_id -> что-то;
теперь всё живое состояние гильдии собрано в одном объекте, а словарь
один — в MusicPlayerService."""

import asyncio
from dataclasses import dataclass

import discord

from src.application.music.player import GuildPlayer


async def delete_message_quiet(message: discord.Message) -> None:
    try:
        await message.delete()
    except discord.HTTPException:
        pass


@dataclass
class GuildMusicSession:
    player: GuildPlayer
    message: discord.Message | None = None  # сообщение-плеер
    updater_task: asyncio.Task | None = None  # живой прогресс трека
    idle_task: asyncio.Task | None = None  # отсчёт до авто-выхода
    idle_prompt: discord.Message | None = None  # «включить что-то ещё?»
    karaoke_task: asyncio.Task | None = None  # цикл живого текста
    karaoke_message: discord.Message | None = None  # сообщение караоке

    def cancel_tasks(self) -> None:
        """Отменить все фоновые задачи сессии (cleanup / выключение бота)."""
        for task in (self.updater_task, self.idle_task, self.karaoke_task):
            if task is not None:
                task.cancel()
