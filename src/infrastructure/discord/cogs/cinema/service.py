"""Фоновая оркестрация киноклуба: переходы по таймерам (закрытие опроса,
напоминание, сбор и подведение итогов оценок, вердикт Попоси).

Выделено из кога: команды и колбэки вью остаются на коге (вью зовут его
хендлеры), а сюда переехали сценарии, которые запускает планировщик. Вью,
которым нужна ссылка на ког, создаются через фабрики — сервис не знает о коге."""

import functools
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import discord

from src.application.ai_chat.mood import MoodTracker
from src.application.ai_chat.service import ChatService
from src.application.cinema.di import CinemaContainer
from src.domain.cinema.entities import MovieEntry, MovieNight
from src.infrastructure.discord.accent import accent
from src.infrastructure.discord.persona_phrase import PersonaPhraseMixin
from src.infrastructure.discord.scheduler import DeferredScheduler
from src.infrastructure.persona_service import RegistryPersona

from .formatting import _SCORE_RE, _title_of, _trim, _ts
from .forum import CinemaForum

logger = logging.getLogger(__name__)

ViewFactory = Callable[[], discord.ui.View]


class CinemaService(PersonaPhraseMixin):
    def __init__(
        self,
        cinema: CinemaContainer,
        bot: discord.Client,
        chat: ChatService | None,
        mood: MoodTracker,
        scheduler: DeferredScheduler,
        forum: CinemaForum,
        watched_view: ViewFactory,
        rating_view: ViewFactory,
        persona=None,
    ):
        self._cinema = cinema
        self._bot = bot
        self._chat = chat
        self._mood = mood
        self._scheduler = scheduler
        self._forum = forum
        self._watched_view = watched_view
        self._rating_view = rating_view
        # голос сервиса — каталог фраз персоны (дефолты реестра без PersonaService)
        self.persona = persona if persona is not None else RegistryPersona()

    async def disable_message(self, channel_id: int, message_id: int) -> None:
        if not channel_id or not message_id:
            return
        channel = self._bot.get_channel(channel_id)
        if channel is None:
            return
        try:
            message = await cast(discord.abc.Messageable, channel).fetch_message(message_id)
            await message.edit(view=None)
        except discord.HTTPException:
            pass

    # --- киновечер: закрытие опроса и напоминание ---

    async def close_poll(self, night_id: int) -> None:
        result = await self._cinema.close_poll.execute(night_id)
        if result.status == "gone" or result.night is None:
            return
        night = result.night
        await self.disable_message(night.channel_id, night.poll_message_id)
        channel = self._bot.get_channel(night.channel_id)
        if channel is None:
            return
        messageable = cast(discord.abc.Messageable, channel)
        gid = night.guild_id
        if result.status == "no_votes":
            try:
                await messageable.send(self._p(gid, "cinema.poll_no_votes"))
            except discord.HTTPException:
                pass
            return
        winner = result.winner
        if winner is None:  # статус != no_votes -> победитель есть; страховка для типов
            return
        comment = ""
        if self._chat is not None:
            try:
                comment = await self._chat.freeform_remark(
                    gid,
                    self._p(
                        gid,
                        "cinema.winner_comment_instruction",
                        title=winner.title,
                        year=f" ({winner.year})" if winner.year else "",
                    ),
                    datetime.now(UTC),
                    mood=self._mood.get(gid),
                )
            except Exception:
                logger.warning("Комментарий к победителю не сгенерировался", exc_info=True)
        votes_total = sum((result.votes or {}).values())
        embed = discord.Embed(
            title=self._p(gid, "cinema.winner_title", title=_trim(_title_of(winner), 200)),
            description=(
                (f"{_trim(winner.overview, 300)}\n\n" if winner.overview else "")
                + self._p(
                    gid,
                    "cinema.night_session",
                    when=_ts(night.scheduled_at, "F"),
                    rel=_ts(night.scheduled_at),
                )
                + "\n"
                + self._p(gid, "cinema.winner_votes", votes=votes_total)
            )[:4000],
            color=accent(gid),
        )
        if winner.poster_url:
            embed.set_thumbnail(url=winner.poster_url)
        try:
            message = await messageable.send(
                content=comment[:1500] or None,
                embed=embed,
                view=self._watched_view(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.warning("Не удалось объявить победителя киновечера", exc_info=True)
            return
        await self._cinema.register_message.execute(
            "winner", cast(int, night.id), channel.id, message.id
        )
        self._scheduler.schedule(
            f"remind:{night.id}", night.scheduled_at, functools.partial(self.remind, night)
        )

    async def remind(self, night: MovieNight) -> None:
        pending = await self._cinema.list_pending.execute()
        if not any(n.id == night.id for n in pending.scheduled):
            return  # отменили или уже смотрят
        winner = await self._cinema.get_movie.execute(night.winner_entry_id or 0)
        if winner is None:
            return
        channel = self._bot.get_channel(night.channel_id)
        if channel is None:
            return
        try:
            await cast(discord.abc.Messageable, channel).send(
                self._p(night.guild_id, "cinema.remind", title=_trim(_title_of(winner), 120))
            )
        except discord.HTTPException:
            pass

    # --- оценки: карточка сбора и подведение итогов ---

    async def post_rating_message(
        self, channel: discord.abc.Messageable, entry: MovieEntry
    ) -> None:
        gid = entry.guild_id
        embed = discord.Embed(
            title=self._p(gid, "cinema.rating_title", title=_trim(_title_of(entry), 200)),
            description=self._p(
                gid, "cinema.rating_body", when=_ts(cast(datetime, entry.rating_ends_at))
            ),
            color=accent(gid),
        )
        if entry.poster_url:
            embed.set_thumbnail(url=entry.poster_url)
        embed.set_footer(text=self._p(gid, "cinema.rating_footer", count=0, reviews=0))
        try:
            message = await channel.send(embed=embed, view=self._rating_view())
        except discord.HTTPException:
            logger.warning("Не удалось открыть сбор оценок", exc_info=True)
            return
        await self._cinema.register_message.execute(
            "rating", cast(int, entry.id), message.channel.id, message.id
        )
        self._scheduler.schedule(
            f"rating:{entry.id}",
            cast(datetime, entry.rating_ends_at),
            functools.partial(self.finalize_rating, cast(int, entry.id)),
        )

    async def _poposya_verdict(self, entry: MovieEntry, guild_id: int) -> tuple[int | None, str]:
        if self._chat is None:
            return None, ""
        try:
            text = await self._chat.freeform_remark(
                guild_id,
                self._p(
                    guild_id,
                    "cinema.verdict_instruction",
                    title=entry.title,
                    year=f" ({entry.year})" if entry.year else "",
                ),
                datetime.now(UTC),
                mood=self._mood.get(guild_id),
            )
        except Exception:
            logger.warning("Вердикт Попоси не сгенерировался", exc_info=True)
            return None, ""
        match = _SCORE_RE.search(text)
        score = min(10, max(1, int(match.group(1)))) if match else None
        return score, text.strip()[:300]

    async def finalize_rating(self, entry_id: int) -> None:
        entry = await self._cinema.get_movie.execute(entry_id)
        if entry is None or entry.status != "rating":
            return
        score, review = await self._poposya_verdict(entry, entry.guild_id)
        result = await self._cinema.finalize_rating.execute(
            entry_id,
            datetime.now(UTC),
            poposya_score=score,
            poposya_review=review,
        )
        if result is None:
            return
        await self.disable_message(entry.channel_id, entry.rating_message_id)
        final = result.entry
        embed = self._forum.summary_embed(final, result.avg, result.count)
        channel = self._bot.get_channel(entry.channel_id)

        # приоритет — форум «золотой фонд»: отдельный пост по фильму со сводкой
        # и всеми рецензиями. В канале просмотра остаётся короткий указатель.
        forum_link = await self._forum.publish(final, embed)
        if forum_link is not None:
            if channel is not None:
                try:
                    await cast(discord.abc.Messageable, channel).send(
                        self._p(
                            final.guild_id,
                            "cinema.finalized_forum",
                            title=_trim(_title_of(final), 100),
                            link=forum_link,
                        ),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    pass
            return

        # фолбэк (форум не задан/недоступен): итоги и ветка прямо в канале
        if channel is None:
            return
        try:
            summary_message = await cast(discord.abc.Messageable, channel).send(embed=embed)
        except discord.HTTPException:
            logger.warning("Не удалось отправить итоги оценок", exc_info=True)
            return
        await self._forum.post_reviews_thread(summary_message, final)
