"""Еженедельный AI-дайджест: раз в неделю (воскресенье вечером) бот постит в
заданный канал итоги недели голосом персоны. Ког тонкий — сбор среза в use case
(BuildWeeklyDigest), тон и текст в слое форматирования; здесь только Discord
(резолв имён, расписание, пост). При сбое ИИ — шаблонный текст без ИИ.

Расписание в UTC: пер-серверных таймзон у бота нет. Воскресенье 17:00 UTC ≈
20:00 МСК — «вечер» для основной аудитории. Дедуп по ISO-неделе (в памяти:
переживает такты, но не рестарт — как дедуп снапшотов активности)."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import cast

import discord
from discord import app_commands
from discord.ext import commands

from src.application.digest.format import (
    TONE_HINTS,
    DigestLine,
    DigestView,
    digest_tone,
    facts_block,
    render_plain,
    weekday_name,
)
from src.config import Settings
from src.infrastructure.discord.feature_flags import block_if_module_off, require_tier
from src.infrastructure.discord.interaction_ctx import guild_of

logger = logging.getLogger(__name__)

_CHECK_INTERVAL = 3600  # почасовой такт — дёшево, окно «вечер воскресенья» ловим точно
_DIGEST_WEEKDAY = 6  # воскресенье (Mon=0..Sun=6)
_DIGEST_HOUR_UTC = 17  # ≈20:00 МСК — вечер основной аудитории
_MAX_POST = 2000  # лимит одного сообщения Discord


class DigestCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        build_weekly_digest,
        chat,
        settings: Settings,
        guild_settings=None,
        entitlements=None,
    ):
        self.bot = bot
        self.build = build_weekly_digest
        self.chat = chat
        self.settings = settings
        self.gs = guild_settings
        self.entitlements = entitlements
        self._loop_started = False
        self._task: asyncio.Task | None = None
        # (guild_id, iso_year, iso_week) уже опубликованные — не дублируем за вечер
        self._posted: set[tuple[int, int, int]] = set()

    def _cfg(self, guild_id: int, key: str):
        default = getattr(self.settings, key)
        return self.gs.get(guild_id, key, default) if self.gs is not None else default

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await block_if_module_off(interaction, self.settings, self.gs, "digest_enabled"):
            return False
        return await require_tier(interaction, self.entitlements, "digest_enabled")

    def cog_unload(self) -> None:  # type: ignore[override]  # discord.py допускает и sync
        if self._task is not None:
            self._task.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._loop_started:
            return
        self._loop_started = True
        self._task = asyncio.create_task(self._schedule_loop())
        logger.info("Дайджест: планировщик запущен (воскресенье ~%02d:00 UTC)", _DIGEST_HOUR_UTC)

    async def _schedule_loop(self) -> None:
        while True:
            await asyncio.sleep(_CHECK_INTERVAL)
            try:
                await self._tick(datetime.now(UTC))
            except Exception:
                logger.exception("Дайджест: такт планировщика упал — продолжаю")

    async def _tick(self, now: datetime) -> None:
        if now.weekday() != _DIGEST_WEEKDAY or now.hour < _DIGEST_HOUR_UTC:
            return
        iso_year, iso_week, _ = now.isocalendar()
        for guild in list(self.bot.guilds):
            key = (guild.id, iso_year, iso_week)
            if key in self._posted:
                continue
            if not self._cfg(guild.id, "digest_enabled"):
                continue
            channel_id = int(self._cfg(guild.id, "digest_channel") or 0)
            if not channel_id:
                continue
            try:
                await self._post(guild, channel_id, now)
            except Exception:
                logger.exception("Дайджест: не удалось опубликовать для %s", guild.id)
            self._posted.add(key)  # даже при пустом/сбое — не долбим весь вечер

    async def _post(self, guild: discord.Guild, channel_id: int, now: datetime) -> None:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return
        text = await self._compose(guild, now)
        if text is None:
            return
        await cast(discord.abc.Messageable, channel).send(
            text[:_MAX_POST], allowed_mentions=discord.AllowedMentions.none()
        )

    async def _compose(self, guild: discord.Guild, now: datetime) -> str | None:
        """Текст дайджеста: AI, при сбое — шаблон. None = рассказывать нечего."""
        digest = await self.build.execute(guild.id, now)
        if digest.is_empty:
            return None
        view = self._to_view(guild, digest)
        tone = TONE_HINTS[digest_tone(view)]
        try:
            text = await self.chat.weekly_digest(guild.id, facts_block(view), tone, now)
            if text:
                return text
        except Exception:
            logger.warning("Дайджест: ИИ недоступен, шаблон для %s", guild.id, exc_info=True)
        return render_plain(view)

    def _name(self, guild: discord.Guild, user_id: int) -> str | None:
        member = guild.get_member(user_id)
        return member.display_name if member is not None else None

    def _to_view(self, guild: discord.Guild, digest) -> DigestView:
        stars = tuple(
            DigestLine(name, f"{p.metric} очк.")
            for p in digest.stars
            if (name := self._name(guild, p.user_id)) is not None
        )
        birthdays = tuple(
            (name, b.in_days)
            for b in digest.birthdays
            if (name := self._name(guild, b.user_id)) is not None
        )
        collector = None
        if digest.top_collector is not None:
            cname = self._name(guild, digest.top_collector.user_id)
            if cname is not None:
                collector = DigestLine(cname, f"{digest.top_collector.metric} наход.")
        peak_name = (
            weekday_name(digest.peak_day)
            if digest.peak_day is not None and digest.peak_day_messages > 0
            else ""
        )
        return DigestView(
            week_start=digest.week_start,
            week_end=digest.week_end,
            messages=digest.messages,
            messages_delta=digest.messages - digest.messages_prev,
            voice_hours=round(digest.voice_hours),
            voice_delta=round(digest.voice_hours - digest.voice_hours_prev),
            members_delta=digest.members_delta,
            peak_day_name=peak_name,
            peak_day_messages=digest.peak_day_messages,
            stars=stars,
            birthdays=birthdays,
            top_collector=collector,
            watched_titles=digest.watched_titles,
        )

    # --- ручной запуск (проверка/по требованию) ---

    @app_commands.command(name="digest", description="Опубликовать дайджест недели сейчас")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def digest_now(self, interaction: discord.Interaction) -> None:
        guild = guild_of(interaction)
        gid = guild.id
        channel_id = int(self._cfg(gid, "digest_channel") or 0)
        if not channel_id:
            await interaction.response.send_message(
                "Канал дайджеста не задан — укажи «Канал недельного дайджеста» в настройках.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        text = await self._compose(guild, datetime.now(UTC))
        if text is None:
            await interaction.followup.send("За неделю пока нечего рассказать.", ephemeral=True)
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            await interaction.followup.send("Канал дайджеста не найден.", ephemeral=True)
            return
        await cast(discord.abc.Messageable, channel).send(
            text[:_MAX_POST], allowed_mentions=discord.AllowedMentions.none()
        )
        await interaction.followup.send("Дайджест опубликован.", ephemeral=True)
