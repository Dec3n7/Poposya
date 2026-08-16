"""MusicCog — тонкий биндинг слеш-команд к музыкальным сервисам.

Логика живёт в service/lyrics/radio/views; здесь — только разбор
аргументов команд и ответы пользователю."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import cast

import discord
from discord import app_commands
from discord.ext import commands

from src.application.interfaces.entitlements import PlanTier
from src.application.music.di import MusicContainer
from src.domain.music.entities import Track
from src.domain.music.exceptions import TrackResolveError
from src.infrastructure.audio.lyrics import LrclibLyricsClient
from src.infrastructure.audio.spotify import SpotifyLinkResolver
from src.infrastructure.discord.cogs.music.formatting import (
    EMBED_COLOR,
    fmt_duration,
    parse_duration,
    trim,
)
from src.infrastructure.discord.cogs.music.lyrics import LyricsService
from src.infrastructure.discord.cogs.music.radio import RadioService
from src.infrastructure.discord.cogs.music.service import MusicPlayerService
from src.infrastructure.discord.cogs.music.views import (
    HistoryView,
    LikedListView,
    PlayerView,
    QueueView,
    SearchView,
)
from src.infrastructure.discord.feature_flags import block_if_module_off, limit_suffix
from src.infrastructure.discord.interaction_ctx import guild_of, member_of
from src.infrastructure.discord.persona_phrase import PersonaPhraseMixin
from src.infrastructure.discord.presence import PresenceService
from src.infrastructure.persona_service import RegistryPersona


class SaveQueueModal(discord.ui.Modal, title="Сохранить очередь как плейлист"):
    """Окно ввода названия при сохранении текущей очереди из плеера."""

    name: discord.ui.TextInput = discord.ui.TextInput(
        label="Название плейлиста",
        placeholder="например: вечерний чилл",
        max_length=50,
        required=True,
    )

    def __init__(self, cog: "MusicCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog._do_save_queue(interaction, str(self.name.value))


logger = logging.getLogger(__name__)

# Параллельность YouTube-поиска при разборе Spotify-плейлиста: полсотни
# последовательных extract'ов не уложились бы в таймаут, а без предела мы
# завалили бы и тредпул yt-dlp, и сам YouTube. Шесть — компромисс.
_SPOTIFY_SEARCH_CONCURRENCY = 6

# free-потолок личных лайков (не через YouTube → можно тарифить). Premium/Pro
# получают полный music_liked_max_per_user. Плеер/очередь/радио — free целиком.
_FREE_LIKED_MAX = 20


class MusicCog(PersonaPhraseMixin, commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        container: MusicContainer,
        guild_settings=None,
        persona=None,
        entitlements=None,
    ):
        self.bot = bot
        self.container = container
        self.settings = container.settings
        self.gs = guild_settings
        self.entitlements = entitlements  # тариф: гейт караоке-live (Premium)
        # голос кога — каталог фраз персоны (дефолты реестра без PersonaService)
        self.persona = persona if persona is not None else RegistryPersona()
        self.audio = container.audio_source
        self.spotify = SpotifyLinkResolver(
            self.settings.spotify_client_id, self.settings.spotify_client_secret
        )
        # композиция музыкального модуля: сервисы и их взаимные связи.
        # PresenceService — единый владелец статуса: музыка отдаёт ему играющий
        # трек, а без музыки он крутит занятия из жизни Попоси
        self.presence = PresenceService(bot, self.settings.presence_rotate_minutes)
        self.service = MusicPlayerService(bot, container, self.presence, persona=self.persona)
        self.lyrics = LyricsService(
            LrclibLyricsClient(),
            self.settings,
            self.service.get_session,
            self.service.spawn,
            guild_settings=guild_settings,
            persona=self.persona,
            is_free=self._is_free,  # кнопка 📜 гейтит караоке-live как /lyrics live
        )
        self.radio = RadioService(bot, container, self.service.get_session)
        self.service.radio = self.radio
        self.service.prefetch_lyrics = self.lyrics.prefetch
        self.service.view_factory = self._make_player_view

    def _make_player_view(self) -> PlayerView:
        return PlayerView(
            self.service,
            self.lyrics,
            self.toggle_like_current,
            self.save_queue_current,
            persona=self.persona,
        )

    async def cog_load(self) -> None:
        # персистентный view: кнопки плеера работают и после рестарта бота
        # (на осиротевших сообщениях вежливо ответят «плеер не активен»)
        self.bot.add_view(self._make_player_view())
        self.presence.start()  # «живой» статус: занятия Попоси, пока нет музыки

    async def cog_unload(self) -> None:
        self.presence.stop()
        await self.service.shutdown()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await block_if_module_off(interaction, self.settings, self.gs, "music_enabled")

    def _is_free(self, guild_id: int) -> bool:
        """Сервер на free-тарифе — для гейта не-YouTube музыкальных фич (лайки >20,
        караоке-live). Нет провайдера тарифов (тесты) → не-free, не гейтим."""
        return self.entitlements is not None and self.entitlements.tier(guild_id) < PlanTier.PREMIUM

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        await self.service.handle_voice_state(member, before, after)

    # --- воспроизведение ---

    @app_commands.command(
        name="play", description="Включить трек: YouTube/Spotify-ссылка или поиск"
    )
    @app_commands.describe(query="Ссылка (YouTube, Spotify-трек/плейлист) или название трека")
    @app_commands.guild_only()
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        await self._play_request(interaction, query, to_front=False)

    @app_commands.command(name="playnext", description="Поставить трек первым в очереди")
    @app_commands.describe(query="Ссылка (YouTube, Spotify-трек/плейлист) или название трека")
    @app_commands.guild_only()
    async def playnext(self, interaction: discord.Interaction, query: str) -> None:
        await self._play_request(interaction, query, to_front=True)

    async def _play_request(
        self, interaction: discord.Interaction, query: str, to_front: bool
    ) -> None:
        member = member_of(interaction)
        gid = guild_of(interaction).id
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                self._p(gid, "music.join_voice_first"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        added = self._p(gid, "music.add_front_prefix" if to_front else "music.add_prefix")

        # Spotify: прямого стриминга нет — узнаём названия и ищем на YouTube.
        # Плейлист/альбом (нужен API), затем одиночный трек (через oEmbed).
        if self.spotify.is_spotify_link(query) and not self.spotify.is_track_link(query):
            if not self.spotify.is_collection_link(query):
                await interaction.followup.send(
                    self._p(gid, "music.spotify_no_title"), ephemeral=True
                )
                return
            if not self.spotify.has_api_credentials:
                await interaction.followup.send(
                    self._p(gid, "music.spotify_playlist_needs_api"), ephemeral=True
                )
                return
            queries = await self.spotify.track_queries_for(
                query, limit=self.settings.music_playlist_limit
            )
            if not queries:
                await interaction.followup.send(
                    self._p(gid, "music.spotify_playlist_failed"), ephemeral=True
                )
                return
            tracks = await self._spotify_youtube_tracks(queries, member.id)
            if not tracks:
                await interaction.followup.send(
                    self._p(gid, "music.spotify_playlist_failed"), ephemeral=True
                )
                return
            if await self.service.enqueue_tracks(interaction, tracks, to_front=to_front):
                await interaction.followup.send(
                    self._p(
                        gid,
                        "music.spotify_playlist_added",
                        prefix=added,
                        found=len(tracks),
                        total=len(queries),
                    ),
                    ephemeral=True,
                )
            return

        # Spotify: одиночный трек через oEmbed -> поиск на YouTube
        if self.spotify.is_spotify_link(query):
            search_query = await self.spotify.search_query_for(query)
            if not search_query:
                await interaction.followup.send(
                    self._p(gid, "music.spotify_no_title"), ephemeral=True
                )
                return
            results = await self.audio.search(search_query, requested_by=member.id, limit=1)
            if not results:
                await interaction.followup.send(
                    self._p(gid, "music.youtube_not_found", query=search_query), ephemeral=True
                )
                return
            if await self.service.enqueue_tracks(interaction, results[:1], to_front=to_front):
                await interaction.followup.send(
                    f"{added} (Spotify → YouTube): **{results[0].title}**", ephemeral=True
                )
            return

        if query.startswith(("http://", "https://")):
            try:
                tracks = await self.audio.resolve(
                    query,
                    requested_by=member.id,
                    playlist_limit=self.settings.music_playlist_limit,
                )
            except TrackResolveError as exc:
                await interaction.followup.send(
                    self._p(gid, "music.link_open_failed", error=trim(str(exc), 300)),
                    ephemeral=True,
                )
                return
            if not tracks:
                await interaction.followup.send(self._p(gid, "music.link_empty"), ephemeral=True)
                return
            if await self.service.enqueue_tracks(interaction, tracks, to_front=to_front):
                text = (
                    self._p(gid, "music.added_playlist_tracks", prefix=added, count=len(tracks))
                    if len(tracks) > 1
                    else f"{added}: **{tracks[0].title}**"
                )
                await interaction.followup.send(text, ephemeral=True)
            return

        results = await self.audio.search(
            query, requested_by=member.id, limit=self.settings.music_search_limit
        )
        if not results:
            await interaction.followup.send(self._p(gid, "music.search_empty"), ephemeral=True)
            return
        await interaction.followup.send(
            self._p(gid, "music.search_results", count=len(results)),
            view=SearchView(
                self.service.enqueue_tracks, results, to_front=to_front, persona=self.persona
            ),
            ephemeral=True,
        )

    async def _spotify_youtube_tracks(self, queries: list[str], requested_by: int) -> list[Track]:
        """Ищет каждый трек плейлиста на YouTube (по одному результату), сохраняя
        порядок и пропуская ненайденное. Параллельность ограничена семафором —
        см. _SPOTIFY_SEARCH_CONCURRENCY."""
        semaphore = asyncio.Semaphore(_SPOTIFY_SEARCH_CONCURRENCY)

        async def one(query: str) -> Track | None:
            async with semaphore:
                try:
                    results = await self.audio.search(query, requested_by=requested_by, limit=1)
                except Exception:
                    logger.warning(
                        "YouTube-поиск трека Spotify не удался: %s", query, exc_info=True
                    )
                    return None
                return results[0] if results else None

        found = await asyncio.gather(*(one(query) for query in queries))
        return [track for track in found if track is not None]

    @app_commands.command(name="queue", description="Показать очередь треков")
    @app_commands.guild_only()
    async def queue(self, interaction: discord.Interaction) -> None:
        player = self.service.get_player(guild_of(interaction).id)
        if player is None or (player.current is None and not player.queue):
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "music.queue_empty"), ephemeral=True
            )
            return
        view = QueueView(player, persona=self.persona)
        await interaction.response.send_message(
            embed=view.build_embed(),
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="history", description="Недавние треки — можно сыграть снова")
    @app_commands.guild_only()
    async def history(self, interaction: discord.Interaction) -> None:
        player = self.service.get_player(guild_of(interaction).id)
        if player is None or not player.history:
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "music.history_empty"), ephemeral=True
            )
            return
        recent = list(reversed(player.history))[:25]  # свежие первыми, лимит select
        lines = [
            f"`{i}.` {trim(t.title, 60)} — {fmt_duration(t.duration)}"
            for i, t in enumerate(recent, 1)
        ]
        embed = discord.Embed(
            title=self._p(
                guild_of(interaction).id, "music.history_title", count=len(player.history)
            ),
            description="\n".join(lines)[:4000],
            color=EMBED_COLOR,
        )
        view = HistoryView(self.service.enqueue_tracks, recent, persona=self.persona)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # --- сохранение очереди из кнопки плеера ---

    async def save_queue_current(self, interaction: discord.Interaction) -> None:
        player = self.service.get_player(guild_of(interaction).id)
        if player is None or not player.all_tracks():
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "music.save_queue_empty"), ephemeral=True
            )
            return
        await interaction.response.send_modal(SaveQueueModal(self))

    async def _do_save_queue(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        gid = guild_of(interaction).id
        player = self.service.get_player(gid)
        tracks = player.all_tracks() if player else []
        error = await self.container.save_playlist.execute(gid, name, interaction.user.id, tracks)
        replies = {
            "": self._p(gid, "music.playlist_saved", name=name.strip()[:50], count=len(tracks)),
            "empty": self._p(gid, "music.save_queue_empty"),
            "limit": self._p(
                gid, "music.playlist_limit", limit=self.settings.music_playlist_max_per_guild
            ),
        }
        await interaction.followup.send(replies[error], ephemeral=True)

    @app_commands.command(name="skip", description="Пропустить текущий трек")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction) -> None:
        player = self.service.get_player(guild_of(interaction).id)
        if player is None or player.current is None:
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "music.nothing_playing"), ephemeral=True
            )
            return
        await interaction.response.send_message(
            self._p(guild_of(interaction).id, "music.skipped"), ephemeral=True
        )
        await player.skip()

    @app_commands.command(name="shuffle", description="Перемешать очередь")
    @app_commands.guild_only()
    async def shuffle(self, interaction: discord.Interaction) -> None:
        player = self.service.get_player(guild_of(interaction).id)
        if player is None or not player.queue:
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "music.nothing_to_shuffle"), ephemeral=True
            )
            return
        await player.shuffle()
        await interaction.response.send_message(
            self._p(guild_of(interaction).id, "music.shuffled"), ephemeral=True
        )

    @app_commands.command(name="remove", description="Убрать трек из очереди по номеру")
    @app_commands.describe(position="Номер трека в очереди (как в /queue)")
    @app_commands.guild_only()
    async def remove(self, interaction: discord.Interaction, position: int) -> None:
        gid = guild_of(interaction).id
        player = self.service.get_player(gid)
        if player is None or not player.queue:
            await interaction.response.send_message(
                self._p(gid, "music.queue_empty"), ephemeral=True
            )
            return
        count = len(player.queue)
        removed = await player.remove_at(position)
        if removed is None:
            await interaction.response.send_message(
                self._p(gid, "music.remove_no_track", position=position, count=count),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            self._p(gid, "music.removed", title=trim(removed.title, 100)),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="seek", description="Перемотать текущий трек: 1:23, 83 или 1:02:03")
    @app_commands.describe(position="Позиция: «1:23», секунды «83» или «1:02:03»")
    @app_commands.guild_only()
    async def seek(self, interaction: discord.Interaction, position: str) -> None:
        gid = guild_of(interaction).id
        player = self.service.get_player(gid)
        if player is None or player.current is None:
            await interaction.response.send_message(
                self._p(gid, "music.nothing_playing"), ephemeral=True
            )
            return
        seconds = parse_duration(position)
        if seconds is None:
            await interaction.response.send_message(
                self._p(gid, "music.seek_bad_position"), ephemeral=True
            )
            return
        if not await player.seek(seconds):
            track = player.current
            if track is not None and track.duration is None:
                msg = self._p(gid, "music.seek_live")
            else:
                limit = fmt_duration(track.duration) if track else "?"
                msg = self._p(gid, "music.seek_out_of_range", limit=limit)
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await interaction.response.send_message(
            self._p(gid, "music.seeked", position=fmt_duration(seconds)), ephemeral=True
        )

    @app_commands.command(name="stop", description="Остановить музыку и выйти из канала")
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        gid = guild_of(interaction).id
        if self.service.get_session(gid) is None:
            await interaction.response.send_message(
                self._p(gid, "music.not_playing_idle"), ephemeral=True
            )
            return
        await interaction.response.send_message(self._p(gid, "music.stopped"), ephemeral=True)
        await self.service.cleanup(gid, self._p(gid, "music.cleanup_stop"))

    @app_commands.command(name="pause", description="Пауза")
    @app_commands.guild_only()
    async def pause(self, interaction: discord.Interaction) -> None:
        gid = guild_of(interaction).id
        player = self.service.get_player(gid)
        if player is None or player.current is None:
            await interaction.response.send_message(
                self._p(gid, "music.pause_nothing"), ephemeral=True
            )
            return
        if player.is_paused:
            await interaction.response.send_message(
                self._p(gid, "music.already_paused"), ephemeral=True
            )
            return
        await player.toggle_pause()
        await interaction.response.send_message(self._p(gid, "music.paused"), ephemeral=True)

    @app_commands.command(name="resume", description="Продолжить воспроизведение")
    @app_commands.guild_only()
    async def resume(self, interaction: discord.Interaction) -> None:
        gid = guild_of(interaction).id
        player = self.service.get_player(gid)
        if player is None or player.current is None or not player.is_paused:
            await interaction.response.send_message(
                self._p(gid, "music.resume_nothing"), ephemeral=True
            )
            return
        await player.toggle_pause()
        await interaction.response.send_message(self._p(gid, "music.resumed"), ephemeral=True)

    @app_commands.command(name="volume", description="Громкость 0–200%")
    @app_commands.describe(value="Процент громкости (0–200)")
    @app_commands.guild_only()
    async def volume(
        self, interaction: discord.Interaction, value: app_commands.Range[int, 0, 200]
    ) -> None:
        gid = guild_of(interaction).id
        player = self.service.get_player(gid)
        if player is None:
            await interaction.response.send_message(
                self._p(gid, "music.nothing_playing"), ephemeral=True
            )
            return
        await player.set_volume(value / 100)
        await interaction.response.send_message(
            self._p(gid, "music.volume_set", value=value), ephemeral=True
        )

    @app_commands.command(name="nowplaying", description="Что сейчас играет")
    @app_commands.guild_only()
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        player = self.service.get_player(guild_of(interaction).id)
        if player is None or player.current is None:
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "music.nowplaying_silence"), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=self.service.build_embed(player), ephemeral=True
        )

    @app_commands.command(name="leave", description="Выйти из голосового канала")
    @app_commands.guild_only()
    async def leave(self, interaction: discord.Interaction) -> None:
        gid = guild_of(interaction).id
        if self.service.get_session(gid) is None:
            await interaction.response.send_message(
                self._p(gid, "music.not_in_voice"), ephemeral=True
            )
            return
        await interaction.response.send_message(self._p(gid, "music.left"), ephemeral=True)
        await self.service.cleanup(gid, self._p(gid, "music.cleanup_leave"))

    # --- текст трека ---

    @app_commands.command(name="lyrics", description="Текст текущего трека")
    @app_commands.describe(live="Караоке-режим: текст абзацами синхронно с треком")
    @app_commands.guild_only()
    async def lyrics_command(self, interaction: discord.Interaction, live: bool = False) -> None:
        gid = guild_of(interaction).id
        # караоке-live — Premium (текст с lrclib, не YouTube). Статичный текст ниже
        # остаётся free. Гейт ДО defer.
        if live and self._is_free(gid):
            await interaction.response.send_message(
                self._p(gid, "music.karaoke_premium"), ephemeral=True
            )
            return
        player = self.service.get_player(gid)
        if player is None or player.current is None:
            await interaction.response.send_message(
                self._p(gid, "music.nothing_playing"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        track = player.current
        synced, plain = await self.lyrics.get(track)  # обычно уже в кэше

        if live:
            if await self.lyrics.start_karaoke(
                cast(discord.abc.Messageable, interaction.channel), gid, track, synced
            ):
                await interaction.followup.send(self._p(gid, "music.karaoke_on"), ephemeral=True)
            else:
                await interaction.followup.send(
                    self._p(gid, "music.karaoke_no_synced"), ephemeral=True
                )
            return

        if not plain:
            await interaction.followup.send(self._p(gid, "music.lyrics_not_found"), ephemeral=True)
            return
        await interaction.followup.send(embed=self.lyrics.plain_embed(track, plain), ephemeral=True)

    @app_commands.command(
        name="lyricsfile", description="Загрузить свой .lrc для караоке текущего трека"
    )
    @app_commands.describe(file="Файл .lrc с таймкодами [mm:ss.xx]")
    @app_commands.guild_only()
    async def lyrics_file(self, interaction: discord.Interaction, file: discord.Attachment) -> None:
        gid = guild_of(interaction).id
        player = self.service.get_player(gid)
        if player is None or player.current is None:
            await interaction.response.send_message(
                self._p(gid, "music.nothing_playing"), ephemeral=True
            )
            return
        if not file.filename.lower().endswith(".lrc") or file.size > 200_000:
            await interaction.response.send_message(
                self._p(gid, "music.lrc_bad_file"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            raw = (await file.read()).decode("utf-8", errors="replace")
        except discord.HTTPException:
            await interaction.followup.send(
                self._p(gid, "music.lrc_download_failed"), ephemeral=True
            )
            return
        if not self.lyrics.set_synced_lrc(player.current.video_id, raw):
            await interaction.followup.send(self._p(gid, "music.lrc_no_timecodes"), ephemeral=True)
            return
        await interaction.followup.send(self._p(gid, "music.lrc_accepted"), ephemeral=True)

    # --- плейлисты сервера ---

    playlist_group = app_commands.Group(
        name="playlist", description="Плейлисты сервера", guild_only=True
    )

    @playlist_group.command(name="save", description="Сохранить текущую очередь как плейлист")
    @app_commands.describe(name="Название плейлиста")
    async def playlist_save(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        gid = guild_of(interaction).id
        player = self.service.get_player(gid)
        tracks = player.all_tracks() if player else []
        error = await self.container.save_playlist.execute(gid, name, interaction.user.id, tracks)
        replies = {
            "": self._p(gid, "music.playlist_saved", name=name.strip()[:50], count=len(tracks)),
            "empty": self._p(gid, "music.playlist_save_empty"),
            "limit": self._p(
                gid, "music.playlist_limit", limit=self.settings.music_playlist_max_per_guild
            ),
        }
        await interaction.followup.send(replies[error], ephemeral=True)

    @playlist_group.command(name="play", description="Включить сохранённый плейлист")
    @app_commands.describe(name="Название плейлиста")
    async def playlist_play(self, interaction: discord.Interaction, name: str) -> None:
        member = member_of(interaction)
        gid = guild_of(interaction).id
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                self._p(gid, "music.join_voice_first"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        tracks = await self.container.load_playlist.execute(gid, name, member.id)
        if tracks is None:
            await interaction.followup.send(
                self._p(gid, "music.playlist_not_found"), ephemeral=True
            )
            return
        if await self.service.enqueue_tracks(interaction, tracks):
            await interaction.followup.send(
                self._p(gid, "music.playlist_played", name=name.strip(), count=len(tracks)),
                ephemeral=True,
            )

    @playlist_group.command(name="list", description="Список плейлистов сервера")
    async def playlist_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        gid = guild_of(interaction).id
        items = await self.container.list_playlists.execute(gid)
        if not items:
            await interaction.followup.send(
                self._p(gid, "music.playlist_list_empty"), ephemeral=True
            )
            return
        lines = [
            f"`{i}.` **{name}** — {count} треков (<@{creator}>)"
            for i, (name, count, creator) in enumerate(items, 1)
        ]
        embed = discord.Embed(
            title=self._p(gid, "music.playlist_list_title", count=len(items)),
            description="\n".join(lines)[:4000],
            color=EMBED_COLOR,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @playlist_group.command(name="delete", description="Удалить плейлист (автор или админ)")
    @app_commands.describe(name="Название плейлиста")
    async def playlist_delete(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        gid = guild_of(interaction).id
        result = await self.container.delete_playlist.execute(
            gid,
            name,
            interaction.user.id,
            member_of(interaction).guild_permissions.administrator,
        )
        replies = {
            "ok": self._p(gid, "music.playlist_deleted", name=name.strip()),
            "not_found": self._p(gid, "music.playlist_not_found"),
            "forbidden": self._p(gid, "music.playlist_delete_forbidden"),
        }
        await interaction.followup.send(replies[result], ephemeral=True)

    # --- совместимость вкусов ---

    # порог совместимости -> ключ вердикта в music.taste_lines (dict)
    _TASTE_TIERS = ((75, "suspicious"), (50, "high"), (25, "mid"), (1, "low"), (0, "none"))

    @app_commands.command(name="taste", description="Совместимость музыкальных вкусов — по лайкам")
    @app_commands.describe(user="С кем сравнить вкусы")
    @app_commands.guild_only()
    async def taste(self, interaction: discord.Interaction, user: discord.Member) -> None:
        gid = guild_of(interaction).id
        if user.bot:
            await interaction.response.send_message(self._p(gid, "music.taste_bot"), ephemeral=True)
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                self._p(gid, "music.taste_self"), ephemeral=True
            )
            return
        await interaction.response.defer()
        mine = await self.container.list_liked.execute(interaction.user.id)
        theirs = await self.container.list_liked.execute(user.id)
        if not mine or not theirs:
            whose = (
                self._p(gid, "music.taste_whose_you")
                if not mine
                else self._p(gid, "music.taste_whose_other", name=user.display_name)
            )
            await interaction.followup.send(
                self._p(gid, "music.taste_no_likes", whose=whose), ephemeral=True
            )
            return
        my_ids = {t.video_id: t for t in mine}
        common = [t for t in theirs if t.video_id in my_ids]
        # процент от меньшей коллекции: честно к тем, у кого лайков мало
        percent = round(100 * len(common) / min(len(mine), len(theirs)))
        tier = next(key for bound, key in self._TASTE_TIERS if percent >= bound)
        verdicts = self.persona.phrase(gid, "music.taste_lines")
        line = verdicts.get(tier, "") if isinstance(verdicts, dict) else ""
        parts = [
            f"{interaction.user.mention} × {user.mention}",
            f"# {percent}%",
            line,
        ]
        if common:
            parts.append("")
            parts.append(self._p(gid, "music.taste_common_header"))
            parts.extend(f"🎵 {trim(t.title, 70)}" for t in common[:5])
            if len(common) > 5:
                parts.append(self._p(gid, "music.taste_common_more", count=len(common) - 5))
        embed = discord.Embed(
            title=self._p(gid, "music.taste_title"),
            description="\n".join(parts)[:4000],
            color=EMBED_COLOR,
        )
        embed.set_footer(
            text=self._p(
                gid,
                "music.taste_footer",
                mine=len(mine),
                theirs=len(theirs),
                other=user.display_name,
            )
        )
        await interaction.followup.send(
            embed=embed, allowed_mentions=discord.AllowedMentions.none()
        )

    # --- радио ---

    @app_commands.command(
        name="radio",
        description="Радио: когда очередь пустеет, я сама ставлю треки из лайков слушателей",
    )
    @app_commands.guild_only()
    async def radio_command(self, interaction: discord.Interaction) -> None:
        guild_id = guild_of(interaction).id
        enabled = self.radio.toggle(guild_id)
        if not enabled:
            await interaction.response.send_message(
                self._p(guild_id, "music.radio_off"), ephemeral=True
            )
            return
        await interaction.response.send_message(self._p(guild_id, "music.radio_on"), ephemeral=True)
        # если прямо сейчас тишина — начинаем не дожидаясь следующего idle
        player = self.service.get_player(guild_id)
        if player is not None and player.current is None:
            self.service.cancel_idle(guild_id)
            if not await self.radio.fill(guild_id):
                await interaction.followup.send(
                    self._p(guild_id, "music.radio_nothing"), ephemeral=True
                )

    # --- лайки ---

    async def toggle_like_current(self, interaction: discord.Interaction) -> None:
        """Кнопка ❤️: личный лайк текущего трека (у каждого свой список)."""
        gid = guild_of(interaction).id
        player = self.service.get_player(gid)
        if player is None or player.current is None:
            await interaction.response.send_message(
                self._p(gid, "music.nothing_playing"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        track = player.current
        # лайки — личная коллекция (не через YouTube → тарифим): free ≤20,
        # Premium/Pro — полный music_liked_max_per_user. Гейт по тарифу гильдии,
        # где нажали ❤️.
        cap = _FREE_LIKED_MAX if self._is_free(gid) else self.settings.music_liked_max_per_user
        status = await self.container.toggle_like.execute(
            interaction.user.id, track, datetime.now(UTC), max_per_user=cap
        )
        title = trim(track.title, 100)
        replies = {
            "liked": self._p(gid, "music.like_liked", title=title),
            "unliked": self._p(gid, "music.like_unliked", title=title),
            # на free упёрлись в 20 — намекнём, что Premium расширяет список
            "limit": self._p(gid, "music.like_limit", limit=cap) + limit_suffix(self.gs, gid),
        }
        await interaction.followup.send(replies[status], ephemeral=True)

    liked_group = app_commands.Group(
        name="liked", description="Твои лайкнутые треки", guild_only=True
    )

    async def _liked_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        liked = await self.container.list_liked.execute(interaction.user.id)
        needle = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for item in liked:
            label = f"{item.title} — {item.uploader}" if item.uploader else item.title
            if needle in label.lower():
                choices.append(app_commands.Choice(name=trim(label, 100), value=item.video_id))
            if len(choices) == 25:
                break
        return choices

    @liked_group.command(name="list", description="Лайкнутые треки — свои или другого участника")
    @app_commands.describe(user="Чьи лайки посмотреть (по умолчанию — свои)")
    async def liked_list(
        self, interaction: discord.Interaction, user: discord.Member | None = None
    ) -> None:
        gid = guild_of(interaction).id
        target = user or interaction.user
        if target.bot:
            await interaction.response.send_message(
                self._p(gid, "music.liked_list_bot"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        liked = await self.container.list_liked.execute(target.id)
        if not liked:
            text = (
                self._p(gid, "music.liked_list_empty_self")
                if target.id == interaction.user.id
                else self._p(gid, "music.liked_list_empty_other", name=target.display_name)
            )
            await interaction.followup.send(text, ephemeral=True)
            return
        view = LikedListView(
            self.container.resolve_liked,
            self.service.enqueue_tracks,
            target,
            liked,
            guild_id=gid,
            persona=self.persona,
        )
        await interaction.followup.send(
            embed=view.build_embed(interaction.user.id), view=view, ephemeral=True
        )

    @liked_group.command(name="play", description="Включить трек из своих лайков")
    @app_commands.describe(track="Трек из твоих лайков")
    @app_commands.autocomplete(track=_liked_autocomplete)
    async def liked_play(self, interaction: discord.Interaction, track: str) -> None:
        member = member_of(interaction)
        gid = guild_of(interaction).id
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                self._p(gid, "music.join_voice_first"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        # оживление: video_id мог умереть — use case сам найдёт замену по названию
        resolved = await self.container.resolve_liked.execute(
            member.id, track, requested_by=member.id
        )
        if resolved is None:
            await interaction.followup.send(self._p(gid, "music.liked_play_dead"), ephemeral=True)
            return
        if await self.service.enqueue_tracks(interaction, [resolved]):
            await interaction.followup.send(
                self._p(gid, "music.liked_play_added", title=trim(resolved.title, 100)),
                ephemeral=True,
            )

    @liked_group.command(name="all", description="Добавить все лайкнутые треки в очередь")
    async def liked_all(self, interaction: discord.Interaction) -> None:
        member = member_of(interaction)
        gid = guild_of(interaction).id
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                self._p(gid, "music.join_voice_first"), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        liked = await self.container.list_liked.execute(member.id)
        if not liked:
            await interaction.followup.send(self._p(gid, "music.liked_all_empty"), ephemeral=True)
            return
        limit = self.settings.music_playlist_limit
        tracks = [t.to_track(member.id) for t in liked[:limit]]
        if await self.service.enqueue_tracks(interaction, tracks):
            note = self._p(gid, "music.liked_all_note", limit=limit) if len(liked) > limit else ""
            await interaction.followup.send(
                self._p(gid, "music.liked_all_queued", count=len(tracks), note=note),
                ephemeral=True,
            )

    @liked_group.command(name="remove", description="Убрать трек из лайков")
    @app_commands.describe(track="Трек из твоих лайков")
    @app_commands.autocomplete(track=_liked_autocomplete)
    async def liked_remove(self, interaction: discord.Interaction, track: str) -> None:
        await interaction.response.defer(ephemeral=True)
        gid = guild_of(interaction).id
        removed = await self.container.remove_liked.execute(interaction.user.id, track)
        await interaction.followup.send(
            self._p(gid, "music.liked_remove_done" if removed else "music.liked_remove_none"),
            ephemeral=True,
        )

    @liked_group.command(name="save", description="Сохранить свои лайки как плейлист сервера")
    @app_commands.describe(name="Название плейлиста")
    async def liked_save(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        gid = guild_of(interaction).id
        liked = await self.container.list_liked.execute(interaction.user.id)
        tracks = [t.to_track(interaction.user.id) for t in liked]
        error = await self.container.save_playlist.execute(gid, name, interaction.user.id, tracks)
        replies = {
            "": self._p(gid, "music.liked_saved", name=name.strip()[:50], count=len(tracks)),
            "empty": self._p(gid, "music.liked_save_empty"),
            "limit": self._p(
                gid, "music.playlist_limit", limit=self.settings.music_playlist_max_per_guild
            ),
        }
        await interaction.followup.send(replies[error], ephemeral=True)
