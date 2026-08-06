"""Публикация итогов киновечера: сводка-эмбед, пост в форум «золотой фонд»
и ветки с оценками/рецензиями зрителей. Выделено из кога — цельная работа
с Discord-тредами вокруг завершённого фильма."""

import logging
from collections.abc import Callable
from typing import cast

import discord

from src.application.cinema.di import CinemaContainer
from src.domain.cinema.entities import MovieEntry
from src.infrastructure.discord.accent import accent
from src.infrastructure.discord.persona_phrase import PersonaPhraseMixin
from src.infrastructure.persona_service import RegistryPersona

from .formatting import _title_of, _trim

logger = logging.getLogger(__name__)


class CinemaForum(PersonaPhraseMixin):
    """Отдельный пост по фильму со сводкой и рецензиями. cfg(guild_id, key)
    резолвит настройку (форум-канал) пер-сервер."""

    def __init__(
        self,
        bot: discord.Client,
        cinema: CinemaContainer,
        cfg: Callable[[int, str], object],
        persona=None,
    ):
        self._bot = bot
        self._cinema = cinema
        self._cfg = cfg
        # голос сервиса — каталог фраз персоны (дефолты реестра без PersonaService)
        self.persona = persona if persona is not None else RegistryPersona()

    def summary_embed(self, final: MovieEntry, avg: float | None, count: int) -> discord.Embed:
        """Красивая сводка по фильму: оценка сервера + вердикт Попоси."""
        embed = discord.Embed(
            title=f"🎬 {_trim(_title_of(final), 200)}",
            description=_trim(final.overview, 400) if final.overview else None,
            color=accent(final.guild_id),
            timestamp=final.watched_at,
        )
        gid = final.guild_id
        embed.add_field(
            name=self._p(gid, "cinema.summary_score_field"),
            value=(
                self._p(gid, "cinema.summary_score_value", avg=avg, count=count)
                if avg is not None
                else self._p(gid, "cinema.summary_no_ratings")
            ),
            inline=True,
        )
        if final.poposya_score is not None or final.poposya_review:
            head = f"**{final.poposya_score}/10** — " if final.poposya_score is not None else ""
            embed.add_field(
                name=self._p(gid, "cinema.summary_verdict_field"),
                value=_trim(f"{head}{final.poposya_review}", 500) or "—",
                inline=False,
            )
        if final.poster_url:
            embed.set_thumbnail(url=final.poster_url)
        embed.set_footer(text=self._p(gid, "cinema.summary_footer"))
        return embed

    async def publish(self, final: MovieEntry, embed: discord.Embed) -> str | None:
        """Публикует пост по фильму в форум-канал «золотой фонд». Возвращает
        ссылку-указатель (mention/jump_url) или None, если форум не настроен
        или публикация не удалась."""
        forum_id = self._cfg(final.guild_id, "cinema_forum_channel")
        if not forum_id:
            return None
        target = self._bot.get_channel(cast(int, forum_id))
        if target is None:
            logger.warning("Форум киноклуба не найден", extra={"channel_id": forum_id})
            return None
        name = _trim(_title_of(final), 100)
        try:
            if isinstance(target, discord.ForumChannel):
                created = await target.create_thread(
                    name=name,
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                thread = created.thread
                await self._post_ratings_into(thread, cast(int, final.id))
                return thread.mention
            if isinstance(target, discord.TextChannel):
                # запасной вариант, если указали обычный текстовый канал
                message = await target.send(embed=embed)
                try:
                    thread = await message.create_thread(name=name)
                    await self._post_ratings_into(thread, cast(int, final.id))
                except discord.HTTPException:
                    pass
                return message.jump_url
        except discord.Forbidden:
            logger.warning("Нет прав публиковать в форум киноклуба", exc_info=True)
            return None
        except discord.HTTPException:
            logger.warning("Не удалось опубликовать фильм в форум", exc_info=True)
            return None
        logger.warning(
            "Канал форума киноклуба неподходящего типа",
            extra={"channel_id": forum_id, "type": type(target).__name__},
        )
        return None

    async def post_reviews_thread(
        self, summary_message: discord.Message, entry: MovieEntry
    ) -> None:
        """Собирает отзывы зрителей в ветку под итоговым сообщением (фолбэк,
        когда форум не настроен)."""
        reviews = await self._cinema.list_reviews.execute(cast(int, entry.id))
        if not reviews:
            return
        try:
            thread = await summary_message.create_thread(
                name=_trim(f"Рецензии: {entry.title}", 100),
            )
        except discord.HTTPException:
            logger.warning("Не удалось создать ветку рецензий", exc_info=True)
            return
        chunk = ""
        for review in reviews:
            score = f"{review.score}/10" if review.score is not None else "без оценки"
            block = f"**<@{review.user_id}>** ({score})\n> {_trim(review.text, 450)}\n\n"
            if len(chunk) + len(block) > 1900:
                await self._send_thread(thread, chunk)
                chunk = ""
            chunk += block
        if chunk:
            await self._send_thread(thread, chunk)

    async def _post_ratings_into(self, thread: discord.Thread, entry_id: int) -> None:
        """Все оценки и рецензии зрителей — сообщениями от лица бота."""
        ratings = await self._cinema.list_ratings.execute(entry_id)
        if not ratings:
            return
        reviewed = [r for r in ratings if r.text.strip()]
        score_only = [r for r in ratings if not r.text.strip() and r.score is not None]

        chunk = ""
        for r in reviewed:
            mark = f"{r.score}/10" if r.score is not None else "без оценки"
            block = f"**<@{r.user_id}>** — {mark}\n> {_trim(r.text, 450)}\n\n"
            if len(chunk) + len(block) > 1900:
                await self._send_thread(thread, chunk)
                chunk = ""
            chunk += block
        if chunk:
            await self._send_thread(thread, chunk)

        if score_only:
            line = "**Оценили без рецензии:** " + " · ".join(
                f"<@{r.user_id}> {r.score}/10" for r in score_only
            )
            await self._send_thread(thread, _trim(line, 1900))

    @staticmethod
    async def _send_thread(thread: discord.Thread, text: str) -> None:
        try:
            await thread.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.warning("Не удалось отправить рецензию в ветку", exc_info=True)
