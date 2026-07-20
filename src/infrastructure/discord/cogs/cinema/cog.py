import logging
from datetime import UTC, date, datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from src.application.ai_chat.mood import MoodTracker
from src.application.ai_chat.service import ChatService
from src.application.cinema.di import CinemaContainer
from src.application.relationship.di import RelationshipContainer
from src.config import Settings
from src.domain.cinema.entities import MovieEntry
from src.infrastructure.cinema.provider import IMovieSearch, MovieInfo
from src.infrastructure.discord.feature_flags import block_if_module_off
from src.infrastructure.discord.scheduler import DeferredScheduler

from .formatting import (
    _DATE_RE,
    _EMBED_COLOR,
    _TIME_RE,
    _title_of,
    _trim,
    _ts,
)
from .forum import CinemaForum
from .service import CinemaService
from .views import (
    CinemaCardView,
    CinemaRatingView,
    CinemaWatchedView,
    MoviePickView,
    NightPollView,
)

logger = logging.getLogger(__name__)


class CinemaCog(commands.Cog):
    """Киноклуб: общий вотчлист с голосованием, киновечера и оценки.
    Попося — полноправный участник: комментирует победителя и ставит
    свой вердикт после просмотра."""

    def __init__(
        self,
        bot: commands.Bot,
        container: CinemaContainer,
        relationship: RelationshipContainer,
        chat_service: ChatService | None,
        settings: Settings,
        mood: MoodTracker,
        movie_search: IMovieSearch,
        guild_settings=None,
    ):
        self.bot = bot
        self.cinema = container
        self.relationship = relationship
        self.chat = chat_service
        self.settings = settings
        self.mood = mood
        self.movie_search = movie_search
        self.gs = guild_settings
        self._scheduler = DeferredScheduler("cinema")
        self._restored = False
        self.forum = CinemaForum(self.bot, self.cinema, self._cfg)
        self.service = CinemaService(
            cinema=self.cinema,
            bot=self.bot,
            chat=self.chat,
            mood=self.mood,
            scheduler=self._scheduler,
            forum=self.forum,
            watched_view=lambda: CinemaWatchedView(self),
            rating_view=lambda: CinemaRatingView(self),
        )

    def _cfg(self, guild_id: int, key: str):
        default = getattr(self.settings, key)
        return self.gs.get(guild_id, key, default) if self.gs is not None else default

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await block_if_module_off(interaction, self.settings, self.gs, "cinema_enabled")

    async def cog_load(self) -> None:
        self.bot.add_view(CinemaCardView(self))
        self.bot.add_view(CinemaWatchedView(self))
        self.bot.add_view(CinemaRatingView(self))

    def cog_unload(self) -> None:
        self._scheduler.cancel_all()

    # --- таймеры с восстановлением после рестарта (см. on_ready) ---

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._restored:
            return
        self._restored = True
        try:
            pending = await self.cinema.list_pending.execute()
        except Exception:
            logger.exception("Не удалось восстановить киноклуб")
            return
        for night in pending.polls:
            candidates = []
            for entry_id in night.candidate_ids:
                entry = await self.cinema.get_movie.execute(entry_id)
                if entry is not None:
                    candidates.append(entry)
            if candidates and night.poll_message_id:
                self.bot.add_view(
                    NightPollView(self, night.id, candidates),
                    message_id=night.poll_message_id,
                )
            self._scheduler.schedule(
                f"poll:{night.id}",
                night.poll_ends_at,
                lambda nid=night.id: self.service.close_poll(nid),
            )
        for night in pending.scheduled:
            self._scheduler.schedule(
                f"remind:{night.id}",
                night.scheduled_at,
                lambda n=night: self.service.remind(n),
            )
        for entry in pending.ratings:
            if entry.rating_ends_at is not None:
                self._scheduler.schedule(
                    f"rating:{entry.id}",
                    entry.rating_ends_at,
                    lambda eid=entry.id: self.service.finalize_rating(eid),
                )
        logger.info(
            "Киноклуб: восстановлено из БД — опросов %d, вечеров %d, сборов оценок %d",
            len(pending.polls),
            len(pending.scheduled),
            len(pending.ratings),
        )

    # --- /movie ---

    movie_group = app_commands.Group(
        name="movie", description="Киноклуб: вотчлист сервера", guild_only=True
    )

    @movie_group.command(name="add", description="Предложить фильм в вотчлист сервера")
    @app_commands.describe(query="Название фильма")
    async def movie_add(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(ephemeral=True)
        results = await self.movie_search.search(query) if self.movie_search.enabled else []
        if len(results) > 1:
            await interaction.followup.send(
                f"Нашла {len(results)} — уточни:",
                view=MoviePickView(self, results),
                ephemeral=True,
            )
            return
        if len(results) == 1:
            await self.add_entry(interaction, results[0])
            return
        # текстовый режим (нет ключа TMDB или ничего не нашлось)
        title = query.strip()[:100]
        if not title:
            await interaction.followup.send("Название-то напиши.", ephemeral=True)
            return
        await self.add_entry(
            interaction,
            MovieInfo(
                tmdb_id=0,
                title=title,
                year=None,
                overview="",
                poster_url="",
            ),
        )

    async def add_entry(self, interaction: discord.Interaction, info: MovieInfo) -> None:
        """Общий путь из /movie add и выбора в MoviePickView (после defer)."""
        entry = MovieEntry(
            guild_id=interaction.guild_id,
            title=info.title,
            added_by=interaction.user.id,
            added_at=datetime.now(UTC),
            tmdb_id=info.tmdb_id or None,
            year=info.year,
            overview=info.overview,
            poster_url=info.poster_url,
        )
        result = await self.cinema.add_movie.execute(entry)
        if result.status == "duplicate":
            await interaction.followup.send(
                f"**{_trim(info.title, 100)}** уже в вотчлисте — голосуй за него 👍.",
                ephemeral=True,
            )
            return
        if result.status == "limit":
            await interaction.followup.send(
                f"Вотчлист переполнен ({self._cfg(interaction.guild_id, 'cinema_watchlist_max')}). "
                "Сначала посмотрите что-нибудь.",
                ephemeral=True,
            )
            return
        saved = result.entry
        embed = discord.Embed(
            title=f"🎬 {_trim(_title_of(saved), 200)}",
            description=(f"{_trim(saved.overview, 350)}\n\n" if saved.overview else "")
            + f"-# Предложил <@{saved.added_by}> · голосуйте кнопками",
            color=_EMBED_COLOR,
        )
        if saved.poster_url:
            embed.set_thumbnail(url=saved.poster_url)
        embed.set_footer(text="👍 0 · 👎 0 · счёт 0")
        try:
            message = await interaction.channel.send(
                embed=embed,
                view=CinemaCardView(self),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            await interaction.followup.send("Не смогла отправить карточку.", ephemeral=True)
            return
        await self.cinema.register_message.execute(
            "card", saved.id, interaction.channel.id, message.id
        )
        await interaction.followup.send(
            f"В вотчлисте: **{_trim(saved.title, 100)}**", ephemeral=True
        )

    async def handle_vote(self, interaction: discord.Interaction, value: int) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await self.cinema.vote_movie.execute(
            interaction.message.id, interaction.user.id, value
        )
        if result.status == "gone":
            await interaction.followup.send("Этот фильм уже не в вотчлисте.", ephemeral=True)
            return
        try:
            embed = interaction.message.embeds[0]
            embed.set_footer(
                text=f"👍 {result.up} · 👎 {result.down} · счёт {result.up - result.down:+d}"
            )
            await interaction.message.edit(embed=embed)
        except discord.HTTPException:
            pass
        note = {1: "👍 Учтён.", -1: "👎 Учтён.", None: "Голос снят."}[result.my_vote]
        await interaction.followup.send(note, ephemeral=True)

    @movie_group.command(name="list", description="Вотчлист сервера по голосам")
    async def movie_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        ranked = await self.cinema.list_watchlist.execute(interaction.guild_id)
        if not ranked:
            await interaction.followup.send(
                "Вотчлист пуст — `/movie add` в помощь.", ephemeral=True
            )
            return
        lines = [
            f"`{i}.` **{_trim(_title_of(e), 60)}** · 👍{up} 👎{down}"
            for i, (e, up, down) in enumerate(ranked[:15], 1)
        ]
        if len(ranked) > 15:
            lines.append(f"…и ещё {len(ranked) - 15}")
        embed = discord.Embed(
            title=f"🎬 Вотчлист ({len(ranked)})",
            description="\n".join(lines)[:4000],
            color=_EMBED_COLOR,
        )
        embed.set_footer(text="/movienight start — устроить киновечер")
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _listed_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        ranked = await self.cinema.list_watchlist.execute(interaction.guild_id)
        needle = current.lower()
        return [
            app_commands.Choice(name=_trim(_title_of(e), 100), value=str(e.id))
            for e, _, _ in ranked
            if needle in e.title.lower()
        ][:25]

    @movie_group.command(name="remove", description="Убрать фильм из вотчлиста (автор или админ)")
    @app_commands.describe(movie="Фильм из вотчлиста")
    @app_commands.autocomplete(movie=_listed_autocomplete)
    async def movie_remove(self, interaction: discord.Interaction, movie: str) -> None:
        await interaction.response.defer(ephemeral=True)
        if not movie.isdigit():
            await interaction.followup.send("Выбери фильм из подсказок.", ephemeral=True)
            return
        status, entry = await self.cinema.remove_movie.execute(
            interaction.guild_id,
            int(movie),
            interaction.user.id,
            interaction.user.guild_permissions.administrator,
        )
        replies = {
            "ok": f"🗑️ Убрала: **{_trim(entry.title if entry else '', 100)}**",
            "not_found": "Такого фильма в вотчлисте нет.",
            "forbidden": "Убирать может предложивший или администратор.",
        }
        await interaction.followup.send(replies[status], ephemeral=True)
        if status == "ok" and entry is not None and entry.message_id:
            channel = self.bot.get_channel(entry.channel_id)
            if channel is not None:
                try:
                    message = await channel.fetch_message(entry.message_id)
                    await message.delete()
                except discord.HTTPException:
                    pass

    @movie_group.command(name="watched", description="Мы посмотрели фильм — открыть сбор оценок")
    @app_commands.describe(movie="Фильм из вотчлиста")
    @app_commands.autocomplete(movie=_listed_autocomplete)
    async def movie_watched(self, interaction: discord.Interaction, movie: str) -> None:
        await interaction.response.defer(ephemeral=True)
        if not movie.isdigit():
            await interaction.followup.send("Выбери фильм из подсказок.", ephemeral=True)
            return
        entry = await self.cinema.open_rating.execute(int(movie), datetime.now(UTC))
        if entry is None:
            await interaction.followup.send(
                "Этот фильм не в вотчлисте или уже оценивается.", ephemeral=True
            )
            return
        await self._post_rating_message(interaction.channel, entry)
        await interaction.followup.send("Открыла сбор оценок.", ephemeral=True)

    @movie_group.command(name="top", description="Золотой фонд: просмотренное по оценкам")
    async def movie_top(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        watched = await self.cinema.top_watched.execute(interaction.guild_id)
        if not watched:
            await interaction.followup.send(
                "Пока ничего не посмотрели. Всё впереди.", ephemeral=True
            )
            return
        lines = []
        for i, e in enumerate(watched[:15], 1):
            score = f"**{e.avg_score}**/10 ({e.ratings_count})" if e.avg_score else "—"
            poposya = f" · я: {e.poposya_score}/10" if e.poposya_score else ""
            lines.append(f"`{i}.` {_trim(_title_of(e), 55)} — {score}{poposya}")
        embed = discord.Embed(
            title=f"🏆 Золотой фонд ({len(watched)})",
            description="\n".join(lines)[:4000],
            color=_EMBED_COLOR,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # --- /movienight ---

    night_group = app_commands.Group(name="movienight", description="Киновечера", guild_only=True)

    def _parse_when(self, date_str: str | None, time_str: str) -> datetime | None:
        """ "20:30" [+ "завтра" | "ДД.ММ"] в UTC; None — не разобрала/в прошлом."""
        tz = timezone(timedelta(hours=self.settings.cinema_utc_offset))
        now_local = datetime.now(tz)
        match = _TIME_RE.match(time_str.strip())
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            return None
        day = now_local.date()
        if date_str:
            raw = date_str.strip().lower()
            if raw == "завтра":
                day = day + timedelta(days=1)
            elif raw != "сегодня":
                dmatch = _DATE_RE.match(raw)
                if not dmatch:
                    return None
                try:
                    day = date(now_local.year, int(dmatch.group(2)), int(dmatch.group(1)))
                except ValueError:
                    return None
        result = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
        if date_str is None and result <= now_local:
            result += timedelta(days=1)  # «в 20:00», а уже вечер — значит завтра
        elif result <= now_local:
            return None
        return result.astimezone(UTC)

    @night_group.command(name="start", description="Устроить киновечер: опрос по топу вотчлиста")
    @app_commands.describe(
        time="Время сеанса, например 20:30",
        day="сегодня (по умолчанию) / завтра / ДД.ММ",
    )
    async def night_start(
        self, interaction: discord.Interaction, time: str, day: str | None = None
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        when = self._parse_when(day, time)
        now = datetime.now(UTC)
        if when is None or when < now + timedelta(minutes=15):
            await interaction.followup.send(
                "Не поняла время. Форматы: `20:30`, `завтра 19:00`, `12.07 21:00`; "
                "минимум за 15 минут.",
                ephemeral=True,
            )
            return
        result = await self.cinema.start_night.execute(
            interaction.guild_id, interaction.user.id, when, now
        )
        if result.status == "busy":
            await interaction.followup.send(
                "Киновечер уже назначен — `/movienight cancel`, если передумали.",
                ephemeral=True,
            )
            return
        if result.status == "empty":
            await interaction.followup.send("Вотчлист пуст — сначала `/movie add`.", ephemeral=True)
            return
        night, candidates = result.night, result.candidates
        lines = [f"`{i}.` **{_trim(_title_of(e), 60)}**" for i, e in enumerate(candidates, 1)]
        embed = discord.Embed(
            title="🍿 Киновечер: выбираем фильм",
            description=(
                "\n".join(lines)
                + f"\n\n**Сеанс:** {_ts(night.scheduled_at, 'F')} ({_ts(night.scheduled_at)})"
                + f"\n**Голосование до** {_ts(night.poll_ends_at, 't')} ({_ts(night.poll_ends_at)})"
            )[:4000],
            color=_EMBED_COLOR,
        )
        embed.set_footer(text="Один голос на человека; можно передумать.")
        try:
            message = await interaction.channel.send(
                embed=embed, view=NightPollView(self, night.id, candidates)
            )
        except discord.HTTPException:
            await interaction.followup.send("Не смогла отправить опрос.", ephemeral=True)
            return
        await self.cinema.register_message.execute(
            "poll", night.id, interaction.channel.id, message.id
        )
        self._scheduler.schedule(
            f"poll:{night.id}",
            night.poll_ends_at,
            lambda nid=night.id: self.service.close_poll(nid),
        )
        await interaction.followup.send("Киновечер объявлен. 🍿", ephemeral=True)

    @night_group.command(name="cancel", description="Отменить киновечер (автор или админ)")
    async def night_cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        status, night = await self.cinema.cancel_night.execute(
            interaction.guild_id,
            interaction.user.id,
            interaction.user.guild_permissions.administrator,
        )
        replies = {
            "ok": "Отменила. Значит, в другой раз.",
            "none": "Отменять нечего — киновечер не назначен.",
            "forbidden": "Отменять может устроивший или администратор.",
        }
        await interaction.followup.send(replies[status], ephemeral=True)
        if status == "ok" and night is not None:
            for key in (f"poll:{night.id}", f"remind:{night.id}"):
                self._scheduler.cancel(key)
            await self.service.disable_message(night.channel_id, night.poll_message_id)

    async def handle_night_vote(self, interaction: discord.Interaction, entry_id: int) -> None:
        await interaction.response.defer(ephemeral=True)
        status = await self.cinema.vote_night.execute(
            interaction.message.id,
            interaction.user.id,
            entry_id,
            datetime.now(UTC),
        )
        if status == "closed":
            await interaction.followup.send("Голосование уже закрыто.", ephemeral=True)
            return
        entry = await self.cinema.get_movie.execute(entry_id)
        title = _trim(entry.title, 80) if entry else "фильм"
        await interaction.followup.send(f"Голос за **{title}** принят.", ephemeral=True)

    # --- оценки ---

    async def handle_begin_rating(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        entry = await self.cinema.open_rating.execute_by_winner_message(
            interaction.message.id, datetime.now(UTC)
        )
        if entry is None:
            await interaction.followup.send(
                "Оценки уже открыты или киновечер не активен.", ephemeral=True
            )
            return
        try:
            await interaction.message.edit(view=None)
        except discord.HTTPException:
            pass
        await self.service.post_rating_message(interaction.channel, entry)
        await interaction.followup.send("Открыла сбор оценок.", ephemeral=True)

    async def handle_rate(self, interaction: discord.Interaction, score: int) -> None:
        await interaction.response.defer(ephemeral=True)
        now = datetime.now(UTC)
        result = await self.cinema.rate_movie.execute(
            interaction.message.id, interaction.user.id, score, now
        )
        if result.status == "closed":
            await interaction.followup.send("Сбор оценок уже закрыт.", ephemeral=True)
            return
        if result.first_time and self.settings.cinema_rating_points > 0:
            try:
                await self.relationship.award_point.execute(
                    interaction.user.id,
                    interaction.guild_id,
                    0,
                    now,
                    base_amount=self.settings.cinema_rating_points,
                )
            except Exception:
                logger.exception("Очки за оценку не начислились")
        try:
            embed = interaction.message.embeds[0]
            embed.set_footer(text=f"Оценок: {result.count} · Отзывов: {result.reviews}")
            await interaction.message.edit(embed=embed)
        except (discord.HTTPException, IndexError):
            pass
        await interaction.followup.send(f"Записала: **{score}/10**.", ephemeral=True)

    async def handle_review(
        self, interaction: discord.Interaction, rating_message_id: int, text: str
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await self.cinema.review_movie.execute(
            rating_message_id, interaction.user.id, text, datetime.now(UTC)
        )
        if result.status == "closed":
            await interaction.followup.send("Сбор оценок уже закрыт.", ephemeral=True)
            return
        # у сабмита модалки нет message — дотягиваем карточку по id, чтобы
        # освежить счётчик отзывов в футере
        try:
            message = await interaction.channel.fetch_message(rating_message_id)
            embed = message.embeds[0]
            embed.set_footer(text=f"Оценок: {result.count} · Отзывов: {result.reviews}")
            await message.edit(embed=embed)
        except (discord.HTTPException, IndexError, AttributeError):
            pass
        await interaction.followup.send(
            "Отзыв записан — появится в ветке под итогами. ✂️👁🖤", ephemeral=True
        )
