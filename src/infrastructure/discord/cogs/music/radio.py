"""📻 Радио: очередь пуста — сама ставлю треки из лайков слушателей.

Состояние радио живёт здесь, а не в GuildMusicSession: включённость и
история переживают пересоздание плеера (cleanup не выключает радио)."""

import logging
import random
import time
from collections import deque
from collections.abc import Callable
from typing import cast

import discord
from discord.ext import commands

from src.application.music.di import MusicContainer
from src.domain.music.entities import Track
from src.infrastructure.discord.cogs.music.session import GuildMusicSession

logger = logging.getLogger(__name__)

_HISTORY_LIMIT = 40
# если предыдущая пачка кончилась быстрее — все треки мертвы, не зацикливаемся
_REFILL_GUARD_SECONDS = 30
_BATCH_SIZE = 3
_MIX_LIMIT = 15  # сколько «похожих» брать из YouTube Mix


class RadioService:
    def __init__(
        self,
        bot: commands.Bot,
        container: MusicContainer,
        get_session: Callable[[int], GuildMusicSession | None],
    ):
        self._bot = bot
        self._container = container
        self._get_session = get_session
        self._enabled: dict[int, bool] = {}
        self._history: dict[int, deque[str]] = {}  # последние video_id
        self._last_fill: dict[int, float] = {}  # monotonic; защита от цикла

    def is_enabled(self, guild_id: int) -> bool:
        return self._enabled.get(guild_id, False)

    def toggle(self, guild_id: int) -> bool:
        enabled = not self.is_enabled(guild_id)
        self._enabled[guild_id] = enabled
        return enabled

    def recently_filled(self, guild_id: int) -> bool:
        return time.monotonic() - self._last_fill.get(guild_id, 0.0) <= _REFILL_GUARD_SECONDS

    async def fill(self, guild_id: int) -> bool:
        """Добрать в очередь пачку треков: лайки сидящих в войсе, иначе
        случайный плейлист сервера. False — источников нет."""
        session = self._get_session(guild_id)
        guild = self._bot.get_guild(guild_id)
        if session is None or guild is None:
            return False
        vc = guild.voice_client
        if vc is None or vc.channel is None:
            return False
        history = self._history.setdefault(guild_id, deque(maxlen=_HISTORY_LIMIT))

        pool: dict[str, Track] = {}
        bot_id = cast(discord.ClientUser, self._bot.user).id  # после ready есть
        for member in cast(discord.VoiceChannel, vc.channel).members:
            if member.bot:
                continue
            for liked in await self._container.list_liked.execute(member.id):
                pool.setdefault(liked.video_id, liked.to_track(bot_id))
        fresh = [t for vid, t in pool.items() if vid not in history]
        if not fresh and pool:
            fresh = list(pool.values())  # всё уже играло — идём по второму кругу

        if not fresh:
            items = await self._container.list_playlists.execute(guild_id)
            if items:
                name = random.choice(items)[0]
                tracks = await self._container.load_playlist.execute(guild_id, name, bot_id)
                fresh = [t for t in tracks or [] if t.video_id not in history] or (tracks or [])
        if not fresh:
            # ни лайков, ни плейлистов — добираем «похожие» из YouTube Mix
            # по последнему игравшему треку (без внешних API)
            fresh = await self._from_mix(session, history)
        if not fresh:
            return False

        batch = random.sample(fresh, min(_BATCH_SIZE, len(fresh)))
        for track in batch:
            history.append(track.video_id)
        self._last_fill[guild_id] = time.monotonic()
        await session.player.enqueue(batch)
        return True

    async def _from_mix(self, session: GuildMusicSession, history: "deque[str]") -> list[Track]:
        """«Похожие» из YouTube Mix (watch?v=<id>&list=RD<id>) по последнему
        игравшему треку. Пустой список — сида нет или микс не отдался."""
        hist = session.player.history
        seed = hist[-1].video_id if hist else None
        if not seed:
            return []
        mix_url = f"https://www.youtube.com/watch?v={seed}&list=RD{seed}"
        try:
            tracks = await self._container.audio_source.resolve(
                mix_url, cast(discord.ClientUser, self._bot.user).id, playlist_limit=_MIX_LIMIT
            )
        except Exception:
            logger.warning("Радио: YouTube Mix не резолвится", exc_info=True)
            return []
        # сам сид и уже игравшее — мимо; если после фильтра пусто, второй круг
        fresh = [t for t in tracks if t.video_id != seed and t.video_id not in history]
        return fresh or [t for t in tracks if t.video_id != seed]
