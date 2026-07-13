"""MusicCog — тонкий биндинг слеш-команд к музыкальным сервисам.

Логика живёт в service/lyrics/radio/views; здесь — только разбор
аргументов команд и ответы пользователю."""

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from src.application.music.di import MusicContainer
from src.domain.music.exceptions import TrackResolveError
from src.infrastructure.audio.lyrics import LrclibLyricsClient
from src.infrastructure.audio.spotify import SpotifyLinkResolver
from src.infrastructure.discord.cogs.music.formatting import (
    EMBED_COLOR,
    fmt_duration,
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


class SaveQueueModal(discord.ui.Modal, title="Сохранить очередь как плейлист"):
    """Окно ввода названия при сохранении текущей очереди из плеера."""

    name = discord.ui.TextInput(
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


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot, container: MusicContainer, guild_settings=None):
        self.bot = bot
        self.container = container
        self.settings = container.settings
        self.audio = container.audio_source
        self.spotify = SpotifyLinkResolver()
        # композиция музыкального модуля: сервисы и их взаимные связи
        self.service = MusicPlayerService(bot, container)
        self.lyrics = LyricsService(
            LrclibLyricsClient(),
            self.settings,
            self.service.get_session,
            self.service.spawn,
            guild_settings=guild_settings,
        )
        self.radio = RadioService(bot, container, self.service.get_session)
        self.service.radio = self.radio
        self.service.prefetch_lyrics = self.lyrics.prefetch
        self.service.view_factory = self._make_player_view

    def _make_player_view(self) -> PlayerView:
        return PlayerView(
            self.service, self.lyrics, self.toggle_like_current, self.save_queue_current
        )

    async def cog_load(self) -> None:
        # персистентный view: кнопки плеера работают и после рестарта бота
        # (на осиротевших сообщениях вежливо ответят «плеер не активен»)
        self.bot.add_view(self._make_player_view())

    async def cog_unload(self) -> None:
        await self.service.shutdown()

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
    @app_commands.describe(query="Ссылка (YouTube, Spotify-трек) или название трека")
    @app_commands.guild_only()
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        await self._play_request(interaction, query, to_front=False)

    @app_commands.command(name="playnext", description="Поставить трек первым в очереди")
    @app_commands.describe(query="Ссылка (YouTube, Spotify-трек) или название трека")
    @app_commands.guild_only()
    async def playnext(self, interaction: discord.Interaction, query: str) -> None:
        await self._play_request(interaction, query, to_front=True)

    async def _play_request(
        self, interaction: discord.Interaction, query: str, to_front: bool
    ) -> None:
        member = interaction.user
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                "Сначала зайди в голосовой канал.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        added = "Поставила первой" if to_front else "Добавила"

        # Spotify: одиночный трек через oEmbed -> поиск на YouTube
        if self.spotify.is_spotify_link(query):
            if "/track/" not in query:
                await interaction.followup.send(
                    "Плейлисты и альбомы Spotify пока не поддерживаются — для них нужен "
                    "их API (SPOTIFY_CLIENT_ID/SECRET в .env). Кидай ссылку на трек.",
                    ephemeral=True,
                )
                return
            search_query = await self.spotify.search_query_for(query)
            if not search_query:
                await interaction.followup.send(
                    "Spotify не отдал название трека. Попробуй поиском.", ephemeral=True
                )
                return
            results = await self.audio.search(search_query, requested_by=member.id, limit=1)
            if not results:
                await interaction.followup.send(
                    f"На YouTube не нашлось: {search_query}", ephemeral=True
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
                    f"Не смогла открыть ссылку: {trim(str(exc), 300)}", ephemeral=True
                )
                return
            if not tracks:
                await interaction.followup.send("По ссылке ничего не нашлось.", ephemeral=True)
                return
            if await self.service.enqueue_tracks(interaction, tracks, to_front=to_front):
                text = (
                    f"{added}: **{len(tracks)}** треков из плейлиста."
                    if len(tracks) > 1
                    else f"{added}: **{tracks[0].title}**"
                )
                await interaction.followup.send(text, ephemeral=True)
            return

        results = await self.audio.search(
            query, requested_by=member.id, limit=self.settings.music_search_limit
        )
        if not results:
            await interaction.followup.send("Ничего не нашла по запросу.", ephemeral=True)
            return
        await interaction.followup.send(
            f"Нашла {len(results)} — выбери трек:",
            view=SearchView(self.service.enqueue_tracks, results, to_front=to_front),
            ephemeral=True,
        )

    @app_commands.command(name="queue", description="Показать очередь треков")
    @app_commands.guild_only()
    async def queue(self, interaction: discord.Interaction) -> None:
        player = self.service.get_player(interaction.guild_id)
        if player is None or (player.current is None and not player.queue):
            await interaction.response.send_message("Очередь пуста.", ephemeral=True)
            return
        view = QueueView(player)
        await interaction.response.send_message(
            embed=view.build_embed(),
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="history", description="Недавние треки — можно сыграть снова")
    @app_commands.guild_only()
    async def history(self, interaction: discord.Interaction) -> None:
        player = self.service.get_player(interaction.guild_id)
        if player is None or not player.history:
            await interaction.response.send_message("История пуста.", ephemeral=True)
            return
        recent = list(reversed(player.history))[:25]  # свежие первыми, лимит select
        lines = [
            f"`{i}.` {trim(t.title, 60)} — {fmt_duration(t.duration)}"
            for i, t in enumerate(recent, 1)
        ]
        embed = discord.Embed(
            title=f"🕘 История ({len(player.history)})",
            description="\n".join(lines)[:4000],
            color=EMBED_COLOR,
        )
        view = HistoryView(self.service.enqueue_tracks, recent)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # --- сохранение очереди из кнопки плеера ---

    async def save_queue_current(self, interaction: discord.Interaction) -> None:
        player = self.service.get_player(interaction.guild_id)
        if player is None or not player.all_tracks():
            await interaction.response.send_message(
                "Очередь пуста — нечего сохранять.", ephemeral=True
            )
            return
        await interaction.response.send_modal(SaveQueueModal(self))

    async def _do_save_queue(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        player = self.service.get_player(interaction.guild_id)
        tracks = player.all_tracks() if player else []
        error = await self.container.save_playlist.execute(
            interaction.guild_id, name, interaction.user.id, tracks
        )
        replies = {
            "": f"💾 Сохранила «{name.strip()[:50]}» — {len(tracks)} треков.",
            "empty": "Очередь пуста — нечего сохранять.",
            "limit": f"Лимит плейлистов на сервере ({self.settings.music_playlist_max_per_guild}) исчерпан.",
        }
        await interaction.followup.send(replies[error], ephemeral=True)

    @app_commands.command(name="skip", description="Пропустить текущий трек")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction) -> None:
        player = self.service.get_player(interaction.guild_id)
        if player is None or player.current is None:
            await interaction.response.send_message("Сейчас ничего не играет.", ephemeral=True)
            return
        await interaction.response.send_message("⏭️ Пропустил.", ephemeral=True)
        await player.skip()

    @app_commands.command(name="shuffle", description="Перемешать очередь")
    @app_commands.guild_only()
    async def shuffle(self, interaction: discord.Interaction) -> None:
        player = self.service.get_player(interaction.guild_id)
        if player is None or not player.queue:
            await interaction.response.send_message("Перемешивать нечего.", ephemeral=True)
            return
        await player.shuffle()
        await interaction.response.send_message("🔀 Перемешал очередь.", ephemeral=True)

    @app_commands.command(name="stop", description="Остановить музыку и выйти из канала")
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        if self.service.get_session(interaction.guild_id) is None:
            await interaction.response.send_message("Я и так ничего не играю.", ephemeral=True)
            return
        await interaction.response.send_message("⏹️ Остановила и вышла.", ephemeral=True)
        await self.service.cleanup(interaction.guild_id, "⏹️ Остановлено командой /stop.")

    @app_commands.command(name="pause", description="Пауза")
    @app_commands.guild_only()
    async def pause(self, interaction: discord.Interaction) -> None:
        player = self.service.get_player(interaction.guild_id)
        if player is None or player.current is None:
            await interaction.response.send_message("Нечего ставить на паузу.", ephemeral=True)
            return
        if player.is_paused:
            await interaction.response.send_message("Уже на паузе.", ephemeral=True)
            return
        await player.toggle_pause()
        await interaction.response.send_message("⏸️ Пауза.", ephemeral=True)

    @app_commands.command(name="resume", description="Продолжить воспроизведение")
    @app_commands.guild_only()
    async def resume(self, interaction: discord.Interaction) -> None:
        player = self.service.get_player(interaction.guild_id)
        if player is None or player.current is None or not player.is_paused:
            await interaction.response.send_message("Продолжать нечего.", ephemeral=True)
            return
        await player.toggle_pause()
        await interaction.response.send_message("▶️ Продолжаю.", ephemeral=True)

    @app_commands.command(name="volume", description="Громкость 0–200%")
    @app_commands.describe(value="Процент громкости (0–200)")
    @app_commands.guild_only()
    async def volume(
        self, interaction: discord.Interaction, value: app_commands.Range[int, 0, 200]
    ) -> None:
        player = self.service.get_player(interaction.guild_id)
        if player is None:
            await interaction.response.send_message("Сейчас ничего не играет.", ephemeral=True)
            return
        await player.set_volume(value / 100)
        await interaction.response.send_message(f"🔊 Громкость: {value}%.", ephemeral=True)

    @app_commands.command(name="nowplaying", description="Что сейчас играет")
    @app_commands.guild_only()
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        player = self.service.get_player(interaction.guild_id)
        if player is None or player.current is None:
            await interaction.response.send_message("Тишина. Наслаждайся.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=self.service.build_embed(player), ephemeral=True
        )

    @app_commands.command(name="leave", description="Выйти из голосового канала")
    @app_commands.guild_only()
    async def leave(self, interaction: discord.Interaction) -> None:
        if self.service.get_session(interaction.guild_id) is None:
            await interaction.response.send_message("Меня и так нет в войсе.", ephemeral=True)
            return
        await interaction.response.send_message("👋 Ушла.", ephemeral=True)
        await self.service.cleanup(interaction.guild_id, "👋 Вышла по команде /leave.")

    # --- текст трека ---

    @app_commands.command(name="lyrics", description="Текст текущего трека")
    @app_commands.describe(live="Караоке-режим: текст абзацами синхронно с треком")
    @app_commands.guild_only()
    async def lyrics_command(self, interaction: discord.Interaction, live: bool = False) -> None:
        player = self.service.get_player(interaction.guild_id)
        if player is None or player.current is None:
            await interaction.response.send_message("Сейчас ничего не играет.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        track = player.current
        synced, plain = await self.lyrics.get(track)  # обычно уже в кэше

        if live:
            if await self.lyrics.start_karaoke(
                interaction.channel, interaction.guild_id, track, synced
            ):
                await interaction.followup.send("🎤 Караоке включено.", ephemeral=True)
            else:
                await interaction.followup.send(
                    "Синхронного текста для этого трека нет — попробуй `/lyrics` без live.",
                    ephemeral=True,
                )
            return

        if not plain:
            await interaction.followup.send(
                "Текста не нашла. Или его нет, или назван криво.", ephemeral=True
            )
            return
        await interaction.followup.send(embed=self.lyrics.plain_embed(track, plain), ephemeral=True)

    @app_commands.command(
        name="lyricsfile", description="Загрузить свой .lrc для караоке текущего трека"
    )
    @app_commands.describe(file="Файл .lrc с таймкодами [mm:ss.xx]")
    @app_commands.guild_only()
    async def lyrics_file(self, interaction: discord.Interaction, file: discord.Attachment) -> None:
        player = self.service.get_player(interaction.guild_id)
        if player is None or player.current is None:
            await interaction.response.send_message("Сейчас ничего не играет.", ephemeral=True)
            return
        if not file.filename.lower().endswith(".lrc") or file.size > 200_000:
            await interaction.response.send_message("Нужен файл .lrc (до 200 КБ).", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            raw = (await file.read()).decode("utf-8", errors="replace")
        except discord.HTTPException:
            await interaction.followup.send("Не смогла скачать файл.", ephemeral=True)
            return
        if not self.lyrics.set_synced_lrc(player.current.video_id, raw):
            await interaction.followup.send(
                "В файле нет строк с таймкодами `[mm:ss.xx]` — это не похоже на .lrc.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            "📄 Приняла твой текст. Жми 📜 в плеере или `/lyrics live` — караоке по нему.",
            ephemeral=True,
        )

    # --- плейлисты сервера ---

    playlist_group = app_commands.Group(
        name="playlist", description="Плейлисты сервера", guild_only=True
    )

    @playlist_group.command(name="save", description="Сохранить текущую очередь как плейлист")
    @app_commands.describe(name="Название плейлиста")
    async def playlist_save(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        player = self.service.get_player(interaction.guild_id)
        tracks = player.all_tracks() if player else []
        error = await self.container.save_playlist.execute(
            interaction.guild_id, name, interaction.user.id, tracks
        )
        replies = {
            "": f"💾 Сохранила «{name.strip()[:50]}» — {len(tracks)} треков.",
            "empty": "Сохранять нечего — очередь пуста.",
            "limit": f"Лимит плейлистов на сервере ({self.settings.music_playlist_max_per_guild}) исчерпан.",
        }
        await interaction.followup.send(replies[error], ephemeral=True)

    @playlist_group.command(name="play", description="Включить сохранённый плейлист")
    @app_commands.describe(name="Название плейлиста")
    async def playlist_play(self, interaction: discord.Interaction, name: str) -> None:
        member = interaction.user
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                "Сначала зайди в голосовой канал.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        tracks = await self.container.load_playlist.execute(interaction.guild_id, name, member.id)
        if tracks is None:
            await interaction.followup.send("Такого плейлиста нет.", ephemeral=True)
            return
        if await self.service.enqueue_tracks(interaction, tracks):
            await interaction.followup.send(
                f"▶️ Плейлист «{name.strip()}»: {len(tracks)} треков в очереди.",
                ephemeral=True,
            )

    @playlist_group.command(name="list", description="Список плейлистов сервера")
    async def playlist_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        items = await self.container.list_playlists.execute(interaction.guild_id)
        if not items:
            await interaction.followup.send(
                "Плейлистов пока нет — `/playlist save`.", ephemeral=True
            )
            return
        lines = [
            f"`{i}.` **{name}** — {count} треков (<@{creator}>)"
            for i, (name, count, creator) in enumerate(items, 1)
        ]
        embed = discord.Embed(
            title=f"💾 Плейлисты ({len(items)})",
            description="\n".join(lines)[:4000],
            color=EMBED_COLOR,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @playlist_group.command(name="delete", description="Удалить плейлист (автор или админ)")
    @app_commands.describe(name="Название плейлиста")
    async def playlist_delete(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await self.container.delete_playlist.execute(
            interaction.guild_id,
            name,
            interaction.user.id,
            interaction.user.guild_permissions.administrator,
        )
        replies = {
            "ok": f"🗑️ «{name.strip()}» удалён.",
            "not_found": "Такого плейлиста нет.",
            "forbidden": "Удалять может только автор плейлиста или администратор.",
        }
        await interaction.followup.send(replies[result], ephemeral=True)

    # --- совместимость вкусов ---

    _TASTE_LINES = (
        (75, "Подозрительно. Один из вас явно списывал. ✂️👁🖤"),
        (50, "Серьёзно? Вам пора вести совместный плейлист."),
        (25, "Неплохо. Есть о чём поговорить за виски."),
        (1, "Слабое пересечение. Но хоть что-то."),
        (0, "Ноль общих треков. Вы вообще на одном сервере сидите?"),
    )

    @app_commands.command(name="taste", description="Совместимость музыкальных вкусов — по лайкам")
    @app_commands.describe(user="С кем сравнить вкусы")
    @app_commands.guild_only()
    async def taste(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if user.bot:
            await interaction.response.send_message(
                "У ботов нет вкуса. Проверено на себе. Хотя у меня — есть.",
                ephemeral=True,
            )
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                "Сравнивать себя с собой? 100%. Поздравляю, нарцисс.", ephemeral=True
            )
            return
        await interaction.response.defer()
        mine = await self.container.list_liked.execute(interaction.user.id)
        theirs = await self.container.list_liked.execute(user.id)
        if not mine or not theirs:
            whose = "у тебя" if not mine else f"у {user.display_name}"
            await interaction.followup.send(
                f"Сравнивать нечего — {whose} пока нет лайков. ❤️ на плеере в помощь.",
                ephemeral=True,
            )
            return
        my_ids = {t.video_id: t for t in mine}
        common = [t for t in theirs if t.video_id in my_ids]
        # процент от меньшей коллекции: честно к тем, у кого лайков мало
        percent = round(100 * len(common) / min(len(mine), len(theirs)))
        line = next(text for bound, text in self._TASTE_LINES if percent >= bound)
        parts = [
            f"{interaction.user.mention} × {user.mention}",
            f"# {percent}%",
            line,
        ]
        if common:
            parts.append("")
            parts.append("**Общие треки:**")
            parts.extend(f"🎵 {trim(t.title, 70)}" for t in common[:5])
            if len(common) > 5:
                parts.append(f"…и ещё {len(common) - 5}")
        embed = discord.Embed(
            title="🎧 Совместимость вкусов",
            description="\n".join(parts)[:4000],
            color=EMBED_COLOR,
        )
        embed.set_footer(text=f"Лайков: {len(mine)} у тебя · {len(theirs)} у {user.display_name}")
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
        guild_id = interaction.guild_id
        enabled = self.radio.toggle(guild_id)
        if not enabled:
            await interaction.response.send_message("📻 Радио выключено.", ephemeral=True)
            return
        await interaction.response.send_message(
            "📻 Радио включено: пустая очередь — не повод для тишины. "
            "Беру треки из лайков тех, кто в войсе, или из плейлистов сервера.",
            ephemeral=True,
        )
        # если прямо сейчас тишина — начинаем не дожидаясь следующего idle
        player = self.service.get_player(guild_id)
        if player is not None and player.current is None:
            self.service.cancel_idle(guild_id)
            if not await self.radio.fill(guild_id):
                await interaction.followup.send(
                    "Пока нечего играть: ни лайков у слушателей, ни плейлистов.",
                    ephemeral=True,
                )

    # --- лайки ---

    async def toggle_like_current(self, interaction: discord.Interaction) -> None:
        """Кнопка ❤️: личный лайк текущего трека (у каждого свой список)."""
        player = self.service.get_player(interaction.guild_id)
        if player is None or player.current is None:
            await interaction.response.send_message("Сейчас ничего не играет.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        track = player.current
        status = await self.container.toggle_like.execute(
            interaction.user.id, track, datetime.now(timezone.utc)
        )
        replies = {
            "liked": f"❤️ В твоих лайках: **{trim(track.title, 100)}** — `/liked`",
            "unliked": f"💔 Убрала из лайков: **{trim(track.title, 100)}**",
            "limit": (
                f"Лимит лайков ({self.settings.music_liked_max_per_user}) исчерпан — "
                "почисти список через `/liked remove`."
            ),
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
        target = user or interaction.user
        if target.bot:
            await interaction.response.send_message("У ботов нет лайков. И вкуса.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        liked = await self.container.list_liked.execute(target.id)
        if not liked:
            text = (
                "Пусто. Жми ❤️ на плеере, когда играет что-то стоящее."
                if target.id == interaction.user.id
                else f"У {target.display_name} пока нет лайков."
            )
            await interaction.followup.send(text, ephemeral=True)
            return
        view = LikedListView(
            self.container.resolve_liked, self.service.enqueue_tracks, target, liked
        )
        await interaction.followup.send(
            embed=view.build_embed(interaction.user.id), view=view, ephemeral=True
        )

    @liked_group.command(name="play", description="Включить трек из своих лайков")
    @app_commands.describe(track="Трек из твоих лайков")
    @app_commands.autocomplete(track=_liked_autocomplete)
    async def liked_play(self, interaction: discord.Interaction, track: str) -> None:
        member = interaction.user
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                "Сначала зайди в голосовой канал.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        # оживление: video_id мог умереть — use case сам найдёт замену по названию
        resolved = await self.container.resolve_liked.execute(
            member.id, track, requested_by=member.id
        )
        if resolved is None:
            await interaction.followup.send(
                "Не смогла оживить этот трек: видео умерло, а поиск замены не дал. "
                "Убери его через `/liked remove`.",
                ephemeral=True,
            )
            return
        if await self.service.enqueue_tracks(interaction, [resolved]):
            await interaction.followup.send(
                f"Добавила из лайков: **{trim(resolved.title, 100)}**", ephemeral=True
            )

    @liked_group.command(name="all", description="Добавить все лайкнутые треки в очередь")
    async def liked_all(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                "Сначала зайди в голосовой канал.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        liked = await self.container.list_liked.execute(member.id)
        if not liked:
            await interaction.followup.send("Лайков пока нет.", ephemeral=True)
            return
        limit = self.settings.music_playlist_limit
        tracks = [t.to_track(member.id) for t in liked[:limit]]
        if await self.service.enqueue_tracks(interaction, tracks):
            note = f" (первые {limit})" if len(liked) > limit else ""
            await interaction.followup.send(
                f"▶️ Все лайки в очереди: **{len(tracks)}** треков{note}. "
                "Умершие видео пропущу сама.",
                ephemeral=True,
            )

    @liked_group.command(name="remove", description="Убрать трек из лайков")
    @app_commands.describe(track="Трек из твоих лайков")
    @app_commands.autocomplete(track=_liked_autocomplete)
    async def liked_remove(self, interaction: discord.Interaction, track: str) -> None:
        await interaction.response.defer(ephemeral=True)
        removed = await self.container.remove_liked.execute(interaction.user.id, track)
        await interaction.followup.send(
            "💔 Убрала." if removed else "Такого трека в твоих лайках нет.",
            ephemeral=True,
        )

    @liked_group.command(name="save", description="Сохранить свои лайки как плейлист сервера")
    @app_commands.describe(name="Название плейлиста")
    async def liked_save(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        liked = await self.container.list_liked.execute(interaction.user.id)
        tracks = [t.to_track(interaction.user.id) for t in liked]
        error = await self.container.save_playlist.execute(
            interaction.guild_id, name, interaction.user.id, tracks
        )
        replies = {
            "": f"💾 Плейлист «{name.strip()[:50]}» из твоих лайков — {len(tracks)} треков.",
            "empty": "Лайков пока нет — сохранять нечего.",
            "limit": f"Лимит плейлистов на сервере ({self.settings.music_playlist_max_per_guild}) исчерпан.",
        }
        await interaction.followup.send(replies[error], ephemeral=True)
