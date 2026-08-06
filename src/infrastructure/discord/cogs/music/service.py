"""Оркестрация музыкальных сессий: жизненный цикл плеера и его UI.

Владеет единственным реестром guild_id -> GuildMusicSession. Ког остаётся
тонким биндингом слеш-команд, views — биндингом кнопок; всё живое — здесь.

Циклические связи (radio/lyrics/views нужны сервису, а им нужен сервис)
разрешаются в композиции кога: radio, prefetch_lyrics и view_factory
внедряются после создания."""

import asyncio
import functools
import logging
import random
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, cast

import discord
from discord.ext import commands

from src.application.music.di import MusicContainer
from src.application.music.player import GuildPlayer
from src.domain.music.entities import Track
from src.domain.player.entities import PlayerState, PlayerTrack
from src.infrastructure.discord.cogs.music.formatting import (
    EMBED_COLOR,
    REPEAT_LABELS,
    fmt_count,
    fmt_duration,
    progress_bar,
    trim,
)
from src.infrastructure.discord.cogs.music.session import (
    GuildMusicSession,
    delete_message_quiet,
)
from src.infrastructure.discord.interaction_ctx import guild_of, member_of
from src.infrastructure.discord.persona_phrase import PersonaPhraseMixin
from src.infrastructure.discord.voice import DiscordVoiceConnection
from src.infrastructure.persona_service import RegistryPersona

if TYPE_CHECKING:
    from src.infrastructure.discord.cogs.music.radio import RadioService

logger = logging.getLogger(__name__)

# сколько ждать перед выходом, когда в войсе не осталось людей (пауза/переход)
_EMPTY_GRACE_SECONDS = 120

# не сыпать репликой на каждый мёртвый трек битого плейлиста
_FAIL_NOTIFY_COOLDOWN = 15


class MusicPlayerService(PersonaPhraseMixin):
    def __init__(self, bot: commands.Bot, container: MusicContainer, presence=None, persona=None):
        self.bot = bot
        self.settings = container.settings
        # голос сервиса — каталог фраз персоны (дефолты реестра без PersonaService)
        self.persona = persona if persona is not None else RegistryPersona()
        self.audio = container.audio_source
        self.bus = container.event_bus
        # снапшот плеера для панели (побочный путь; в тестах-заглушках может не быть)
        self._save_state = getattr(container, "save_player_state", None)
        self.sessions: dict[int, GuildMusicSession] = {}
        self._background: set[asyncio.Task] = set()
        self._empty_grace: dict[int, asyncio.Task] = {}  # таймеры выхода из пустого войса
        self._last_fail_notify: dict[int, float] = {}  # антиспам реплик о провале трека
        # единый владелец Discord-статуса: музыка «слушает трек», иначе — жизнь
        self._presence = presence
        # внедряется композицией (MusicCog) после создания:
        self.radio: RadioService | None = None
        self.prefetch_lyrics: Callable[[Track], None] | None = None
        self.view_factory: Callable[[], discord.ui.View] | None = None

    # --- инфраструктура ---

    async def refresh_presence(self) -> None:
        """Сообщить владельцу presence, что играет сейчас (или что замолчало).
        Presence глобальный — показываем любой играющий трек; когда музыки нет,
        PresenceService сам поставит занятие из жизни Попоси."""
        if self._presence is None:
            return
        playing = next(
            (s.player.current for s in self.sessions.values() if s.player.current),
            None,
        )
        desired = trim(playing.title, 120) if playing is not None else None
        await self._presence.set_now_playing(desired)

    def spawn(self, coro: Coroutine) -> None:
        """Фоновая задача с удержанием ссылки — иначе её может собрать GC."""
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def get_session(self, guild_id: int) -> GuildMusicSession | None:
        return self.sessions.get(guild_id)

    def get_player(self, guild_id: int) -> GuildPlayer | None:
        session = self.sessions.get(guild_id)
        return session.player if session is not None else None

    async def shutdown(self) -> None:
        """Выключение бота: погасить все задачи и прибрать каждую гильдию."""
        for task in list(self._background):
            task.cancel()
        for task in list(self._empty_grace.values()):
            task.cancel()
        for session in self.sessions.values():
            session.cancel_tasks()
        for guild_id in list(self.sessions):
            await self.cleanup(guild_id, self._p(guild_id, "music.cleanup_shutdown"))

    # --- постановка в очередь ---

    async def enqueue_tracks(
        self, interaction: discord.Interaction, tracks: list[Track], to_front: bool = False
    ) -> bool:
        """Общий путь для /play, /playnext, Spotify, лайков и выбора из поиска."""
        session = await self._get_or_create_session(interaction)
        if session is None:
            return False
        gid = cast(int, interaction.guild_id)  # сессия создана -> guild есть
        self.cancel_idle(gid)
        await self._ensure_message(gid, cast(discord.abc.Messageable, interaction.channel))
        await session.player.enqueue(tracks, front=to_front)
        return True

    async def _get_or_create_session(
        self, interaction: discord.Interaction
    ) -> GuildMusicSession | None:
        member = member_of(interaction)
        guild = guild_of(interaction)
        if not member.voice or not member.voice.channel:
            await interaction.followup.send(
                self._p(guild.id, "music.no_longer_in_voice"), ephemeral=True
            )
            return None
        channel = member.voice.channel

        vc = guild.voice_client
        if vc is None:
            try:
                vc = await channel.connect(self_deaf=True)
            except discord.ClientException:
                vc = guild.voice_client
                if vc is None:
                    raise
        elif vc.channel != channel:
            existing = self.sessions.get(guild.id)
            if existing is not None and existing.player.is_playing:
                await interaction.followup.send(
                    self._p(guild.id, "music.already_playing_elsewhere"), ephemeral=True
                )
                return None
            await cast(discord.VoiceClient, vc).move_to(channel)

        session = self.sessions.get(guild.id)
        if session is None:
            voice = DiscordVoiceConnection(cast(discord.VoiceClient, vc), self.settings.ffmpeg_path)
            player = GuildPlayer(
                guild_id=guild.id,
                audio_source=self.audio,
                voice=voice,
                event_bus=self.bus,
                volume=self.settings.music_default_volume,
                prefetch_files=self.settings.music_prefetch_tracks,
            )
            player.on_state_changed = functools.partial(self._on_player_state, guild.id)
            player.on_idle = functools.partial(self._on_idle, guild.id)
            player.on_track_failed = functools.partial(self._on_track_failed, guild.id)
            session = GuildMusicSession(player=player)
            self.sessions[guild.id] = session
        return session

    # --- сообщение плеера и прогресс ---

    def build_embed(self, player: GuildPlayer) -> discord.Embed:
        gid = player.guild_id
        if player.current is None:
            return discord.Embed(
                title=self._p(gid, "music.player_title"),
                description=self._p(gid, "music.player_empty"),
                color=EMBED_COLOR,
            )
        track = player.current
        elapsed = player.elapsed()
        if track.duration:
            progress = (
                f"{progress_bar(elapsed, track.duration)}\n"
                f"`{fmt_duration(elapsed)} / {fmt_duration(track.duration)}`"
            )
        else:
            progress = f"{self._p(gid, 'music.player_live')} · `{fmt_duration(elapsed)}`"
        title = self._p(
            gid, "music.player_paused_title" if player.is_paused else "music.player_now_title"
        )
        # исполнитель/канал + метаданные (просмотры/год) под названием
        meta_parts: list[str] = []
        if track.uploader:
            meta_parts.append(f"🎤 {trim(track.uploader, 80)}")
        meta = self.audio.track_meta(track.video_id)
        if isinstance(meta, dict):
            views = fmt_count(meta.get("view_count"))
            if views:
                meta_parts.append(f"👁 {views}")
            date = meta.get("upload_date")
            if date and len(str(date)) >= 4:
                meta_parts.append(f"📅 {str(date)[:4]}")
        by = f"\n-# {' · '.join(meta_parts)}" if meta_parts else ""
        embed = discord.Embed(
            title=title,
            description=f"**[{trim(track.title, 200)}]({track.url})**{by}\n\n{progress}",
            color=EMBED_COLOR,
        )
        if track.thumbnail:
            embed.set_thumbnail(url=track.thumbnail)
        embed.add_field(
            name=self._p(gid, "music.field_volume"), value=f"{int(player.volume * 100)}%"
        )
        embed.add_field(name=self._p(gid, "music.field_repeat"), value=REPEAT_LABELS[player.repeat])
        embed.add_field(
            name=self._p(gid, "music.field_requester"), value=f"<@{track.requested_by}>"
        )
        if self.radio is not None and self.radio.is_enabled(player.guild_id):
            embed.add_field(
                name=self._p(gid, "music.field_radio"), value=self._p(gid, "music.field_radio_on")
            )
        if player.queue:
            preview = "\n".join(
                f"`{i}.` {trim(t.title, 60)}" for i, t in enumerate(list(player.queue)[:3], 1)
            )
            rest = len(player.queue) - 3
            if rest > 0:
                preview += "\n" + self._p(gid, "music.player_more", rest=rest)
            embed.add_field(name=self._p(gid, "music.field_next"), value=preview, inline=False)
        return embed

    async def _ensure_message(self, guild_id: int, channel: discord.abc.Messageable) -> None:
        session = self.sessions.get(guild_id)
        if session is None:
            return
        message = session.message
        if message is not None and message.channel.id == getattr(channel, "id", None):
            return
        if message is not None:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        assert self.view_factory is not None  # внедряется в MusicCog.__init__
        message = await channel.send(
            embed=self.build_embed(session.player), view=self.view_factory()
        )
        session.message = message
        session.player.text_channel_id = message.channel.id
        self._start_updater(guild_id)

    def _player_snapshot(self, player: GuildPlayer) -> PlayerState:
        def pt(t: Track) -> PlayerTrack:
            return PlayerTrack(
                title=t.title,
                url=t.url,
                duration=t.duration,
                requested_by=t.requested_by,
                uploader=t.uploader,
                thumbnail=t.thumbnail,
            )

        cur = player.current
        return PlayerState(
            guild_id=player.guild_id,
            is_active=cur is not None,
            current=pt(cur) if cur is not None else None,
            queue=[pt(t) for t in player.queue],
            position_seconds=player.elapsed(),
            is_paused=player.is_paused,
            repeat=player.repeat.value,
            volume=player.volume,
        )

    async def _write_snapshot(self, state: PlayerState) -> None:
        # снапшот для панели — побочный путь; сбой БД не должен трогать плеер
        if self._save_state is None:
            return
        try:
            await self._save_state.execute(state)
        except Exception:
            logger.debug("Снапшот плеера не записан", exc_info=True)

    async def _on_player_state(self, guild_id: int) -> None:
        await self._refresh_message(guild_id)
        await self.refresh_presence()
        # префетч текста в кэш при каждом старте трека — кнопка 📜 сработает мгновенно
        player = self.get_player(guild_id)
        if player is not None:
            await self._write_snapshot(self._player_snapshot(player))
            if player.current is not None and self.prefetch_lyrics:
                self.prefetch_lyrics(player.current)

    async def _on_track_failed(self, guild_id: int, track: Track, reason: str) -> None:
        """Трек не удалось включить — сказать об этом в чат вместо молчания.
        С кулдауном: битый плейлист не должен породить залп реплик."""
        now = time.monotonic()
        if now - self._last_fail_notify.get(guild_id, float("-inf")) < _FAIL_NOTIFY_COOLDOWN:
            return
        self._last_fail_notify[guild_id] = now
        player = self.get_player(guild_id)
        channel = (
            self.bot.get_channel(player.text_channel_id)
            if player is not None and player.text_channel_id
            else None
        )
        if channel is None:
            return
        phrases = self.persona.phrase(guild_id, "music.fail_phrases")
        pool = phrases if isinstance(phrases, list) and phrases else [""]
        text = random.choice(pool).format(title=trim(track.title, 100))
        try:
            await cast(discord.abc.Messageable, channel).send(
                text, allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.HTTPException:
            pass

    async def _refresh_message(self, guild_id: int) -> None:
        session = self.sessions.get(guild_id)
        if session is None or session.message is None:
            return
        message = session.message
        try:
            await message.edit(embed=self.build_embed(session.player))
        except discord.NotFound:
            # сообщение удалили — пересоздаём в том же канале
            session.message = None
            await self._ensure_message(guild_id, message.channel)
        except discord.HTTPException:
            logger.warning("Не удалось обновить сообщение плеера", exc_info=True)

    def _start_updater(self, guild_id: int) -> None:
        session = self.sessions.get(guild_id)
        if session is None:
            return
        if session.updater_task is not None and not session.updater_task.done():
            return
        session.updater_task = asyncio.create_task(self._updater_loop(guild_id))

    async def _updater_loop(self, guild_id: int) -> None:
        # Прогресс через редактирование сообщения; интервал не меньше 5 секунд,
        # чтобы не упереться в rate limit Discord на edit.
        interval = max(5, self.settings.music_progress_interval)
        while True:
            await asyncio.sleep(interval)
            player = self.get_player(guild_id)
            if player is None:
                return
            if player.is_playing and not player.is_paused:
                await self._refresh_message(guild_id)

    # --- авто-выход ---

    def cancel_idle(self, guild_id: int) -> None:
        session = self.sessions.get(guild_id)
        if session is None:
            return
        if session.idle_task is not None:
            session.idle_task.cancel()
            session.idle_task = None
        if session.idle_prompt is not None:
            prompt, session.idle_prompt = session.idle_prompt, None
            self.spawn(delete_message_quiet(prompt))

    async def _on_idle(self, guild_id: int) -> None:
        # 📻 радио: очередь пуста — добираем треки сами. Если предыдущая пачка
        # закончилась подозрительно быстро (<30 c — все треки мертвы), не
        # зацикливаемся и уходим в обычный отсчёт простоя.
        if self.radio is not None and self.radio.is_enabled(guild_id):
            if not self.radio.recently_filled(guild_id):
                try:
                    if await self.radio.fill(guild_id):
                        return
                except Exception:
                    logger.exception("Радио не смогло добрать треки")
        await self.refresh_presence()  # очередь кончилась — снять «слушает»
        session = self.sessions.get(guild_id)
        if session is None:
            return
        # очередь закончилась: плеер удаляется сразу; следующий трек создаст
        # свежее сообщение-плеер внизу канала вместо реанимации старого
        if session.message is not None:
            message, session.message = session.message, None
            await delete_message_quiet(message)
        self.cancel_idle(guild_id)
        session.idle_task = asyncio.create_task(self._idle_countdown(guild_id))

    async def _idle_countdown(self, guild_id: int) -> None:
        timeout = self.settings.music_idle_timeout
        warn = max(0, min(self.settings.music_idle_warn_seconds, timeout))
        await asyncio.sleep(timeout - warn)
        player = self.get_player(guild_id)
        if player is None or player.is_playing:
            return
        # за warn секунд до выхода — спросить, не включить ли ещё
        if warn > 0:
            channel = (
                self.bot.get_channel(player.text_channel_id) if player.text_channel_id else None
            )
            if channel is not None:
                try:
                    prompt = await cast(discord.abc.Messageable, channel).send(
                        self._p(guild_id, "music.idle_prompt", minutes=max(1, warn // 60))
                    )
                    session = self.sessions.get(guild_id)
                    if session is not None:
                        session.idle_prompt = prompt
                except discord.HTTPException:
                    pass
            await asyncio.sleep(warn)
        session = self.sessions.get(guild_id)
        if session is not None and not session.player.is_playing:
            # снять СВОЮ задачу с сессии до cleanup: иначе cleanup отменит
            # нас же и умрёт на полпути
            session.idle_task = None
            await self.cleanup(guild_id, self._p(guild_id, "music.cleanup_idle"))

    async def handle_voice_state(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        guild = member.guild
        if guild.id not in self.sessions:
            return
        if member.id == cast(discord.ClientUser, self.bot.user).id:
            # бота отключили — прибираем; переместили — переоценим новый канал ниже
            if after.channel is None:
                await self.cleanup(guild.id, self._p(guild.id, "music.cleanup_kicked"))
                return
        elif member.bot:
            return
        vc = guild.voice_client
        if vc is None or vc.channel is None:
            return
        humans = [m for m in cast(discord.VoiceChannel, vc.channel).members if not m.bot]
        if humans:
            self._cancel_empty_grace(guild.id)  # кто-то есть — отменяем выход
            return
        # никого: не выходим мгновенно (вдруг переключают канал / поставили на
        # паузу и отошли) — даём льготный таймер, потом перепроверяем
        self._start_empty_grace(guild.id)

    def _cancel_empty_grace(self, guild_id: int) -> None:
        task = self._empty_grace.pop(guild_id, None)
        if task is not None:
            task.cancel()

    def _start_empty_grace(self, guild_id: int) -> None:
        if guild_id in self._empty_grace:
            return
        self._empty_grace[guild_id] = asyncio.create_task(self._empty_countdown(guild_id))

    async def _empty_countdown(self, guild_id: int) -> None:
        try:
            await asyncio.sleep(_EMPTY_GRACE_SECONDS)
        except asyncio.CancelledError:
            return
        self._empty_grace.pop(guild_id, None)
        guild = self.bot.get_guild(guild_id)
        vc = guild.voice_client if guild is not None else None
        if vc is None or vc.channel is None:
            return
        if not [m for m in cast(discord.VoiceChannel, vc.channel).members if not m.bot]:
            await self.cleanup(guild_id, self._p(guild_id, "music.cleanup_all_left"))

    # --- завершение ---

    async def cleanup(self, guild_id: int, reason: str) -> None:
        self._cancel_empty_grace(guild_id)
        session = self.sessions.pop(guild_id, None)
        if session is None:
            return
        session.cancel_tasks()
        for msg in (session.idle_prompt, session.karaoke_message):
            if msg is not None:
                self.spawn(delete_message_quiet(msg))
        player = session.player
        player.on_state_changed = None
        player.on_idle = None
        try:
            await player.stop_and_clear()
        except Exception:
            logger.exception("Ошибка при остановке плеера")
        if session.message is not None:
            embed = discord.Embed(
                title=self._p(guild_id, "music.player_title"), description=reason, color=EMBED_COLOR
            )
            try:
                await session.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass
        # панель: плеер остановлен — гасим live-блок
        await self._write_snapshot(PlayerState(guild_id=guild_id, is_active=False))
        await self.refresh_presence()  # больше не играем в этой гильдии
