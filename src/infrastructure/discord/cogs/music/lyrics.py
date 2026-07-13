"""Тексты и караоке: кэш lrclib, префетч при старте трека, живой цикл."""

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Callable, Coroutine

import discord

from src.config import Settings
from src.domain.music.entities import Track
from src.infrastructure.audio.lyrics import LrclibLyricsClient, group_blocks, parse_lrc
from src.infrastructure.discord.cogs.music.formatting import (
    EMBED_COLOR,
    block_index,
    fmt_duration,
    trim,
)
from src.infrastructure.discord.cogs.music.session import (
    GuildMusicSession,
    delete_message_quiet,
)

logger = logging.getLogger(__name__)

_CACHE_LIMIT = 100

# ANSI для цветного караоке (Discord поддерживает ```ansi блоки)
_ANSI_RESET = "[0m"
_ANSI_CURRENT = "[1;36m"  # жирный голубой — текущая строка
_ANSI_DIM = "[0;30m"  # приглушённый серый — остальное

Blocks = list[tuple[float, list[str]]]


class LyricsService:
    """Кэширует тексты (video_id -> (synced, plain)) и ведёт караоке-сессии.

    Караоке-задача и её сообщение хранятся в GuildMusicSession — cleanup
    сессии гасит караоке вместе с остальными задачами."""

    def __init__(
        self,
        client: LrclibLyricsClient,
        settings: Settings,
        get_session: Callable[[int], GuildMusicSession | None],
        spawn: Callable[[Coroutine], None],
        guild_settings=None,
    ):
        self._client = client
        self._settings = settings
        self._get_session = get_session
        self._spawn = spawn
        self._gs = guild_settings
        self._cache: OrderedDict[str, tuple[str | None, str | None]] = OrderedDict()
        self._pending: set[str] = set()

    def _ansi_enabled(self, guild_id: int) -> bool:
        """Цветное караоке включено на сервере? (тумблер /config music_karaoke_ansi)"""
        default = self._settings.music_karaoke_ansi
        return bool(self._gs.get(guild_id, "music_karaoke_ansi", default)) if self._gs else default

    # --- кэш и префетч ---

    async def get(self, track: Track) -> tuple[str | None, str | None]:
        """(synced, plain) из кэша или с запросом; результат кэшируется."""
        cached = self._cache.get(track.video_id)
        if cached is not None:
            self._cache.move_to_end(track.video_id)
            return cached
        result = await self._client.find_both(track.title, track.uploader)
        self._cache[track.video_id] = result
        while len(self._cache) > _CACHE_LIMIT:
            self._cache.popitem(last=False)
        return result

    def set_synced_lrc(self, video_id: str, raw: str) -> bool:
        """Пользовательский .lrc для трека: кладём в кэш как synced-текст.
        False — в файле нет ни одной строки с таймкодом [mm:ss.xx]."""
        if not parse_lrc(raw):
            return False
        _, plain = self._cache.get(video_id, (None, None))
        self._cache[video_id] = (raw, plain)
        self._cache.move_to_end(video_id)
        while len(self._cache) > _CACHE_LIMIT:
            self._cache.popitem(last=False)
        return True

    def prefetch(self, track: Track) -> None:
        """Фоном подтянуть текст в кэш — кнопка 📜 сработает мгновенно."""
        if track.video_id in self._cache or track.video_id in self._pending:
            return
        self._pending.add(track.video_id)

        async def run() -> None:
            try:
                await self.get(track)
            except Exception:
                logger.debug("Префетч текста не удался", exc_info=True)
            finally:
                self._pending.discard(track.video_id)

        self._spawn(run())

    # --- эмбеды ---

    def plain_embed(self, track: Track, text: str) -> discord.Embed:
        embed = discord.Embed(
            title=f"📜 {trim(track.title, 200)}",
            description=text[:4000],
            color=EMBED_COLOR,
        )
        embed.set_footer(text="lrclib.net")
        return embed

    def _karaoke_embed(
        self, title: str, blocks: Blocks, index: int, elapsed: int, ansi: bool = False
    ) -> discord.Embed:
        """Текущий абзац крупно, следующий — приглушённо. ansi — цветной режим."""
        description = (
            self._karaoke_ansi(blocks, index) if ansi else self._karaoke_plain(blocks, index)
        )
        embed = discord.Embed(
            title=f"🎤 {trim(title, 200)}",
            description=description[:4000],
            color=EMBED_COLOR,
        )
        embed.set_footer(
            text=f"{fmt_duration(elapsed)} · абзац {max(index, 0) + 1}/{len(blocks)} · lrclib.net"
        )
        return embed

    @staticmethod
    def _karaoke_plain(blocks: Blocks, index: int) -> str:
        parts: list[str] = []
        if index < 0:
            parts.append("*…сейчас начнётся…*")
            preview = blocks[0][1] if blocks else []
            parts.extend(f"-# {line}" for line in preview[:2])
        else:
            parts.extend(f"**{line}**" for line in blocks[index][1])
            if index + 1 < len(blocks):
                parts.append("")
                parts.extend(f"-# {line}" for line in blocks[index + 1][1][:2])
        return "\n".join(parts)

    @staticmethod
    def _karaoke_ansi(blocks: Blocks, index: int) -> str:
        """ANSI-блок: текущая строка — жирным голубым, остальное — серым."""
        lines: list[str] = []
        if index < 0:
            lines.append(f"{_ANSI_DIM}…сейчас начнётся…{_ANSI_RESET}")
            for line in (blocks[0][1] if blocks else [])[:2]:
                lines.append(f"{_ANSI_DIM}{line}{_ANSI_RESET}")
        else:
            for line in blocks[index][1]:
                lines.append(f"{_ANSI_CURRENT}{line}{_ANSI_RESET}")
            if index + 1 < len(blocks):
                lines.append("")
                for line in blocks[index + 1][1][:2]:
                    lines.append(f"{_ANSI_DIM}{line}{_ANSI_RESET}")
        body = "\n".join(lines)
        return f"```ansi\n{body}\n```"

    # --- караоке ---

    def stop_karaoke(self, guild_id: int) -> bool:
        session = self._get_session(guild_id)
        if session is None or session.karaoke_task is None:
            return False
        task, message = session.karaoke_task, session.karaoke_message
        session.karaoke_task = None
        session.karaoke_message = None
        task.cancel()
        if message is not None:
            self._spawn(delete_message_quiet(message))
        return True

    async def start_karaoke(
        self,
        channel: discord.abc.Messageable,
        guild_id: int,
        track: Track,
        synced: str | None,
    ) -> bool:
        blocks = group_blocks(parse_lrc(synced)) if synced else []
        if not blocks:
            return False
        session = self._get_session(guild_id)
        if session is None:
            return False
        self.stop_karaoke(guild_id)  # одна караоке-сессия на сервер
        player = session.player
        offset = self._settings.music_lyrics_offset
        ansi = self._ansi_enabled(guild_id)
        index = block_index(blocks, player.elapsed_precise() + offset)
        message = await channel.send(
            embed=self._karaoke_embed(track.title, blocks, index, player.elapsed(), ansi)
        )
        task = asyncio.create_task(
            self._live_loop(guild_id, message, blocks, track.video_id, index)
        )
        session.karaoke_task = task
        session.karaoke_message = message
        return True

    async def toggle(self, interaction: discord.Interaction) -> None:
        """Кнопка 📜 в плеере: включает/выключает караоке; текст уже в кэше."""
        guild_id = interaction.guild_id
        if self.stop_karaoke(guild_id):
            await interaction.response.send_message("📜 Караоке выключено.", ephemeral=True)
            return
        session = self._get_session(guild_id)
        if session is None or session.player.current is None:
            await interaction.response.send_message("Сейчас ничего не играет.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        track = session.player.current
        synced, plain = await self.get(track)
        if synced and await self.start_karaoke(interaction.channel, guild_id, track, synced):
            await interaction.followup.send("🎤 Караоке включено.", ephemeral=True)
        elif plain:
            await interaction.followup.send(embed=self.plain_embed(track, plain), ephemeral=True)
        else:
            await interaction.followup.send("Текста для этого трека не нашла.", ephemeral=True)

    async def _live_loop(
        self,
        guild_id: int,
        message: discord.Message,
        blocks: Blocks,
        track_id: str,
        current_index: int = -1,
    ) -> None:
        # Синхронизация: просыпаемся точно к началу следующего абзаца (позиция
        # считается по elapsed_precise + music_lyrics_offset — компенсация
        # буферизации звука), а между абзацами раз в refresh секунд обновляем
        # таймер в футере. Итог: не чаще ~1 edit в 5 c — rate limit не задевается.
        # При смене трека караоке не гаснет: старое сообщение удаляется и сразу
        # отправляется новое с текстом следующего трека.
        refresh = float(max(5, self._settings.music_progress_interval))
        offset = self._settings.music_lyrics_offset
        ansi = self._ansi_enabled(guild_id)
        try:
            while True:
                session = self._get_session(guild_id)
                player = session.player if session is not None else None
                if player is None or player.current is None:
                    # музыка кончилась — караоке тихо сворачиваем
                    await delete_message_quiet(message)
                    return
                if player.current.video_id != track_id:
                    # очередь перешла к новому треку — перезапускаем караоке
                    track = player.current
                    synced, _plain = await self.get(track)  # обычно в кэше
                    new_blocks = group_blocks(parse_lrc(synced)) if synced else []
                    await delete_message_quiet(message)
                    if not new_blocks:
                        return  # синхронного текста нет — караоке выключаем
                    blocks = new_blocks
                    track_id = track.video_id
                    current_index = block_index(blocks, player.elapsed_precise() + offset)
                    try:
                        message = await message.channel.send(
                            embed=self._karaoke_embed(
                                track.title, blocks, current_index, player.elapsed(), ansi
                            )
                        )
                    except discord.HTTPException:
                        logger.warning("Караоке не переехало на новый трек", exc_info=True)
                        return
                    # обновить сообщение в сессии — stop_karaoke удалит актуальное
                    if session is not None and session.karaoke_task is asyncio.current_task():
                        session.karaoke_message = message
                index = block_index(blocks, player.elapsed_precise() + offset)
                # правим сообщение ТОЛЬКО при смене абзаца — иначе чат дёргается
                # от постоянных обновлений таймера (караоке про текст, не про часы)
                if index != current_index:
                    current_index = index
                    try:
                        await message.edit(
                            embed=self._karaoke_embed(
                                player.current.title, blocks, index, player.elapsed(), ansi
                            )
                        )
                    except discord.NotFound:
                        return
                    except discord.HTTPException:
                        logger.warning("Караоке-сообщение не обновилось", exc_info=True)
                if player.is_paused or index + 1 >= len(blocks):
                    delay = refresh
                else:
                    to_next = blocks[index + 1][0] - (player.elapsed_precise() + offset)
                    delay = min(refresh, max(0.2, to_next))
                await asyncio.sleep(delay)
        finally:
            # снять себя с сессии, только если нас не заменила новая караоке-задача
            session = self._get_session(guild_id)
            if session is not None and session.karaoke_task is asyncio.current_task():
                session.karaoke_task = None
                session.karaoke_message = None
