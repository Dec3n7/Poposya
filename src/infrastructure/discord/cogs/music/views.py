"""Discord-виджеты музыкального модуля.

Views зависят от сервисов и колбэков, а не от кога: PlayerView стейтлесс
(guild_id берётся из интеракции), поэтому один экземпляр регистрируется
через bot.add_view — кнопки переживают рестарт бота."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, cast

import discord

from src.domain.music.entities import LikedTrack, Track
from src.infrastructure.discord.cogs.music.formatting import (
    EMBED_COLOR,
    fmt_duration,
    trim,
)
from src.infrastructure.discord.interaction_ctx import guild_of, member_of
from src.infrastructure.discord.persona_phrase import PersonaPhraseMixin
from src.infrastructure.persona_service import RegistryPersona

if TYPE_CHECKING:
    from src.application.music.use_cases import ResolveLikedUseCase
    from src.infrastructure.discord.cogs.music.lyrics import LyricsService
    from src.infrastructure.discord.cogs.music.service import MusicPlayerService

# общий путь постановки в очередь: (interaction, tracks, to_front) -> успех
EnqueueFn = Callable[[discord.Interaction, list[Track], bool], Awaitable[bool]]


class SearchSelect(discord.ui.Select):
    def __init__(
        self, enqueue: EnqueueFn, tracks: list[Track], to_front: bool = False, persona=None
    ):
        self._enqueue = enqueue
        self.tracks = tracks
        self.to_front = to_front
        self.persona = persona if persona is not None else RegistryPersona()
        options = [
            discord.SelectOption(
                label=trim(track.title, 95),
                description=trim(
                    f"{fmt_duration(track.duration)} · {track.uploader or 'YouTube'}", 95
                ),
                value=str(i),
                emoji="🎵",
            )
            for i, track in enumerate(tracks)
        ]
        super().__init__(placeholder="Выбери трек…", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        track = self.tracks[int(self.values[0])]
        key = "music.add_front_prefix" if self.to_front else "music.add_prefix"
        prefix = str(self.persona.phrase(guild_of(interaction).id, key))
        await interaction.response.edit_message(content=f"{prefix}: **{track.title}**", view=None)
        await self._enqueue(interaction, [track], self.to_front)


class SearchView(discord.ui.View):
    def __init__(
        self, enqueue: EnqueueFn, tracks: list[Track], to_front: bool = False, persona=None
    ):
        super().__init__(timeout=60)
        self.add_item(SearchSelect(enqueue, tracks, to_front, persona=persona))


class HistorySelect(discord.ui.Select):
    """Выбор трека из истории для повторного воспроизведения."""

    def __init__(self, enqueue: EnqueueFn, tracks: list[Track], persona=None):
        self._enqueue = enqueue
        self.tracks = tracks
        self.persona = persona if persona is not None else RegistryPersona()
        options = [
            discord.SelectOption(
                label=trim(track.title, 95),
                description=trim(
                    f"{fmt_duration(track.duration)} · {track.uploader or 'YouTube'}", 95
                ),
                value=str(i),
                emoji="🕘",
            )
            for i, track in enumerate(tracks)
        ]
        super().__init__(placeholder="Сыграть снова…", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        from dataclasses import replace

        track = replace(self.tracks[int(self.values[0])], requested_by=interaction.user.id)
        content = str(
            self.persona.phrase(
                guild_of(interaction).id, "music.added_again", title=trim(track.title, 100)
            )
        )
        await interaction.response.edit_message(content=content, view=None)
        await self._enqueue(interaction, [track], False)


class HistoryView(discord.ui.View):
    def __init__(self, enqueue: EnqueueFn, tracks: list[Track], persona=None):
        super().__init__(timeout=120)
        self.add_item(HistorySelect(enqueue, tracks, persona=persona))


_QUEUE_PAGE_SIZE = 10


class QueueView(discord.ui.View):
    """Очередь с пагинацией; читает состояние плеера вживую (эфемерное)."""

    def __init__(self, player, persona=None):
        super().__init__(timeout=120)
        self.player = player
        self.persona = persona if persona is not None else RegistryPersona()
        self.page = 0
        self._sync()

    @property
    def total_pages(self) -> int:
        return max(1, -(-len(self.player.queue) // _QUEUE_PAGE_SIZE))

    def build_embed(self) -> discord.Embed:
        gid = self.player.guild_id
        queue = list(self.player.queue)
        start = self.page * _QUEUE_PAGE_SIZE
        lines: list[str] = []
        current = self.player.current
        if current is not None and self.page == 0:
            lines.append(
                f"▶️ **{trim(current.title, 65)}** — "
                f"{fmt_duration(current.duration)} · <@{current.requested_by}>"
            )
            lines.append("")
        for i, track in enumerate(queue[start : start + _QUEUE_PAGE_SIZE], start=start + 1):
            lines.append(
                f"`{i}.` {trim(track.title, 55)} — "
                f"{fmt_duration(track.duration)} · <@{track.requested_by}>"
            )
        if not queue:
            lines.append(str(self.persona.phrase(gid, "music.queue_only_current")))
        total = sum(t.duration or 0 for t in queue)
        embed = discord.Embed(
            title=str(self.persona.phrase(gid, "music.queue_title", count=len(queue))),
            description="\n".join(lines)[:4000],
            color=EMBED_COLOR,
        )
        embed.set_footer(
            text=str(
                self.persona.phrase(
                    gid,
                    "music.queue_footer",
                    page=self.page + 1,
                    total=self.total_pages,
                    duration=fmt_duration(total),
                )
            )
        )
        return embed

    def _sync(self) -> None:
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages - 1

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.page = min(self.total_pages - 1, self.page + 1)
        self._sync()
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )


_LIKED_PAGE_SIZE = 10


class LikedListView(discord.ui.View):
    """Пагинация лайков (своих или чужих) + запуск трека со страницы.
    Сообщение эфемерное, так что кнопки видит и жмёт только вызвавший."""

    def __init__(
        self,
        resolve_liked: "ResolveLikedUseCase",
        enqueue: EnqueueFn,
        owner: discord.abc.User,
        tracks: list[LikedTrack],
        guild_id: int = 0,
        persona=None,
    ):
        super().__init__(timeout=300)
        self._resolve_liked = resolve_liked
        self._enqueue = enqueue
        self.owner = owner
        self.tracks = tracks
        self._guild_id = guild_id
        self.persona = persona if persona is not None else RegistryPersona()
        self.page = 0
        self._rebuild()

    @property
    def total_pages(self) -> int:
        return max(1, -(-len(self.tracks) // _LIKED_PAGE_SIZE))

    def _page_tracks(self) -> list[LikedTrack]:
        start = self.page * _LIKED_PAGE_SIZE
        return self.tracks[start : start + _LIKED_PAGE_SIZE]

    def build_embed(self, viewer_id: int) -> discord.Embed:
        start = self.page * _LIKED_PAGE_SIZE
        lines = [
            f"`{start + i}.` {trim(t.title, 60)} — {fmt_duration(t.duration)}"
            for i, t in enumerate(self._page_tracks(), 1)
        ]
        title = (
            str(
                self.persona.phrase(
                    self._guild_id, "music.liked_title_self", count=len(self.tracks)
                )
            )
            if self.owner.id == viewer_id
            else str(
                self.persona.phrase(
                    self._guild_id,
                    "music.liked_title_other",
                    name=self.owner.display_name,
                    count=len(self.tracks),
                )
            )
        )
        embed = discord.Embed(
            title=title,
            description="\n".join(lines)[:4000],
            color=EMBED_COLOR,
        )
        embed.set_footer(
            text=str(
                self.persona.phrase(
                    self._guild_id,
                    "music.liked_footer",
                    page=self.page + 1,
                    total=self.total_pages,
                )
            )
        )
        return embed

    def _rebuild(self) -> None:
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages - 1
        self.play_select.options = [  # type: ignore[assignment]  # @ui.select: mypy видит метод
            discord.SelectOption(
                label=trim(track.title, 95),
                description=trim(
                    f"{fmt_duration(track.duration)} · {track.uploader or 'YouTube'}", 95
                ),
                value=track.video_id,
                emoji="🎵",
            )
            for track in self._page_tracks()
        ]

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def prev_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._rebuild()
        await interaction.response.edit_message(
            embed=self.build_embed(interaction.user.id), view=self
        )

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary, row=1)
    async def next_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.page = min(self.total_pages - 1, self.page + 1)
        self._rebuild()
        await interaction.response.edit_message(
            embed=self.build_embed(interaction.user.id), view=self
        )

    @discord.ui.select(placeholder="Включить трек с этой страницы…", row=0)
    async def play_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        member = member_of(interaction)
        gid = guild_of(interaction).id
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                str(self.persona.phrase(gid, "music.join_voice_first")), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        # оживление идёт по списку владельца (умершее видео лечится у него),
        # а заказчиком трека становится нажавший
        resolved = await self._resolve_liked.execute(
            self.owner.id, select.values[0], requested_by=member.id
        )
        if resolved is None:
            await interaction.followup.send(
                str(self.persona.phrase(gid, "music.liked_play_dead_view")), ephemeral=True
            )
            return
        if await self._enqueue(interaction, [resolved], False):
            await interaction.followup.send(
                str(
                    self.persona.phrase(
                        gid, "music.liked_view_added", title=trim(resolved.title, 100)
                    )
                ),
                ephemeral=True,
            )


class PlayerView(PersonaPhraseMixin, discord.ui.View):
    """Кнопки сообщения-плеера. Персистентный view: у всех кнопок custom_id,
    guild_id берётся из интеракции — экземпляр без состояния гильдии."""

    def __init__(
        self,
        service: "MusicPlayerService",
        lyrics: "LyricsService",
        on_like: Callable[[discord.Interaction], Awaitable[None]],
        on_save: Callable[[discord.Interaction], Awaitable[None]] | None = None,
        persona=None,
    ):
        super().__init__(timeout=None)
        self._service = service
        self._lyrics = lyrics
        self._on_like = on_like
        self._on_save = on_save
        self.persona = persona if persona is not None else RegistryPersona()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        player = self._service.get_player(guild_of(interaction).id)
        if player is None:
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "music.player_inactive"), ephemeral=True
            )
            return False
        # лайк — личное дело: доступен всем, кто видит плеер, без войса
        if cast(dict, interaction.data or {}).get("custom_id") == "music:like":
            return True
        vc = guild_of(interaction).voice_client
        member = member_of(interaction)
        if not vc or not member.voice or member.voice.channel != vc.channel:
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "music.buttons_voice_only"), ephemeral=True
            )
            return False
        return True

    # --- ряд 0: управление треками ---

    @discord.ui.button(
        emoji="⏮️", style=discord.ButtonStyle.secondary, row=0, custom_id="music:prev"
    )
    async def prev_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer()
        player = self._service.get_player(guild_of(interaction).id)
        if player is None:
            return
        if not await player.previous():
            await interaction.followup.send(
                self._p(guild_of(interaction).id, "music.history_empty_back"), ephemeral=True
            )

    @discord.ui.button(
        emoji="⏯️", style=discord.ButtonStyle.secondary, row=0, custom_id="music:pause"
    )
    async def pause_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer()
        player = self._service.get_player(guild_of(interaction).id)
        if player is not None:
            await player.toggle_pause()

    @discord.ui.button(
        emoji="⏭️", style=discord.ButtonStyle.secondary, row=0, custom_id="music:next"
    )
    async def next_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer()
        player = self._service.get_player(guild_of(interaction).id)
        if player is not None:
            await player.skip()

    @discord.ui.button(
        emoji="🔁", style=discord.ButtonStyle.secondary, row=0, custom_id="music:repeat"
    )
    async def repeat_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        # без лишних сообщений: режим повтора виден в эмбеде плеера,
        # который обновляется этим же нажатием
        await interaction.response.defer()
        player = self._service.get_player(guild_of(interaction).id)
        if player is not None:
            await player.cycle_repeat()

    @discord.ui.button(
        emoji="⏹️", style=discord.ButtonStyle.secondary, row=0, custom_id="music:stop"
    )
    async def stop_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer()
        await self._service.cleanup(
            guild_of(interaction).id, self._p(guild_of(interaction).id, "music.cleanup_stop_button")
        )

    # --- ряд 1: громкость ---

    @discord.ui.button(
        emoji="🔉", style=discord.ButtonStyle.secondary, row=1, custom_id="music:vol_down"
    )
    async def volume_down_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer()
        player = self._service.get_player(guild_of(interaction).id)
        if player is not None:
            await player.change_volume(-0.1)

    @discord.ui.button(
        emoji="🔊", style=discord.ButtonStyle.secondary, row=1, custom_id="music:vol_up"
    )
    async def volume_up_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer()
        player = self._service.get_player(guild_of(interaction).id)
        if player is not None:
            await player.change_volume(+0.1)

    @discord.ui.button(
        emoji="🔀", style=discord.ButtonStyle.secondary, row=1, custom_id="music:shuffle"
    )
    async def shuffle_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        player = self._service.get_player(guild_of(interaction).id)
        if player is None:
            return
        if len(player.queue) < 2:
            await interaction.response.send_message(
                self._p(guild_of(interaction).id, "music.shuffle_empty_player"), ephemeral=True
            )
            return
        await interaction.response.defer()
        await player.shuffle()  # новый порядок виден в поле «Далее» эмбеда

    @discord.ui.button(
        emoji="📜", style=discord.ButtonStyle.secondary, row=1, custom_id="music:lyrics"
    )
    async def lyrics_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._lyrics.toggle(interaction)

    @discord.ui.button(
        emoji="❤️", style=discord.ButtonStyle.secondary, row=1, custom_id="music:like"
    )
    async def like_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._on_like(interaction)

    # --- ряд 2: сохранить очередь как плейлист ---

    @discord.ui.button(
        emoji="💾",
        label="Сохранить очередь",
        style=discord.ButtonStyle.secondary,
        row=2,
        custom_id="music:savequeue",
    )
    async def save_button(self, interaction: discord.Interaction, _: discord.ui.Button):
        if self._on_save is not None:
            await self._on_save(interaction)
