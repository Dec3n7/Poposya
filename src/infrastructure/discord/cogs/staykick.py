"""Фича «остаться или уйти»: при входе новичка Попося пишет ему в ЛС с двумя
кнопками. «Останусь» — снять авто-кик; «уйти» — кик через N часов (с
напоминанием). Ничего не нажал — остаётся (дефолт). Кик, не бан: можно вернуться.

Кнопки — DynamicItem (guild_id зашит в custom_id), поэтому переживают рестарт.
Сам кик хранится в БД и добивается фоновым циклом — как темп-баны."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import cast

import discord
from discord.ext import commands

from src.application.staykick.di import StayKickContainer
from src.config import Settings
from src.infrastructure.persona_service import RegistryPersona

logger = logging.getLogger(__name__)

_LOOP_INTERVAL_SECONDS = 60

# Подписи кнопок — структурная form-chrome (нужны при инстанцировании
# DynamicItem, где персоны под рукой нет). Голос — банки фраз в реестре.
# Альтернативы: ("Задержусь", "Один вечер, и всё") · ("Я с вами", "Транзит").
BUTTON_STAY = "Останусь"
BUTTON_LEAVE = "Я мимоходом — убери через 12 ч"


def _now() -> datetime:
    return datetime.now(UTC)


class _StayButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sk:stay:(?P<gid>\d+)"):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        super().__init__(
            discord.ui.Button(
                label=BUTTON_STAY,
                style=discord.ButtonStyle.success,
                custom_id=f"sk:stay:{guild_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["gid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = cast(commands.Bot, interaction.client).get_cog("StayKickCog")
        if cog is not None:
            await cog.on_stay(interaction, self.guild_id)  # type: ignore[attr-defined]


class _LeaveButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sk:leave:(?P<gid>\d+)"):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        super().__init__(
            discord.ui.Button(
                label=BUTTON_LEAVE,
                style=discord.ButtonStyle.secondary,
                custom_id=f"sk:leave:{guild_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["gid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = cast(commands.Bot, interaction.client).get_cog("StayKickCog")
        if cog is not None:
            await cog.on_leave(interaction, self.guild_id)  # type: ignore[attr-defined]


def _make_view(guild_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(_StayButton(guild_id))
    view.add_item(_LeaveButton(guild_id))
    return view


class StayKickCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        container: StayKickContainer,
        settings: Settings,
        guild_settings=None,
        persona=None,  # PersonaService — банки фраз (голос кога)
    ):
        self.bot = bot
        self.container = container
        self.settings = settings
        self.gs = guild_settings
        self.persona = persona if persona is not None else RegistryPersona()
        self._loop_task: asyncio.Task | None = None

    def _cfg(self, guild_id: int, key: str):
        default = getattr(self.settings, key)
        return self.gs.get(guild_id, key, default) if self.gs is not None else default

    async def _pick(self, guild_id: int, key: str) -> str:
        """Случайная фраза из банка каталога персоны сервера."""
        return await self.persona.render_block(guild_id, key, None) or ""

    async def cog_load(self) -> None:
        # клики по кнопкам переживают рестарт: регистрируем динамические items
        self.bot.add_dynamic_items(_StayButton, _LeaveButton)

    def cog_unload(self) -> None:  # type: ignore[override]  # discord.py допускает и sync
        if self._loop_task is not None:
            self._loop_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._loop())

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot or not self._cfg(member.guild.id, "staykick_enabled"):
            return
        try:
            prompt = await self._pick(member.guild.id, "staykick.join_prompts")
            await member.send(prompt, view=_make_view(member.guild.id))
        except discord.Forbidden:
            pass  # ЛС закрыты — по дефолту остаётся, молчим
        except discord.HTTPException:
            logger.warning("Не удалось отправить ЛС новичку", exc_info=True)

    # --- колбэки кнопок (в ЛС) ---

    async def on_stay(self, interaction: discord.Interaction, guild_id: int) -> None:
        await self.container.cancel_kick.execute(guild_id, interaction.user.id)
        await self._close(interaction, await self._pick(guild_id, "staykick.stay_replies"))

    async def on_leave(self, interaction: discord.Interaction, guild_id: int) -> None:
        await self.container.schedule_kick.execute(
            guild_id,
            interaction.user.id,
            _now(),
            self._cfg(guild_id, "staykick_hours"),
            self.settings.staykick_remind_before_minutes,
        )
        await self._close(interaction, await self._pick(guild_id, "staykick.leave_replies"))

    @staticmethod
    async def _close(interaction: discord.Interaction, text: str) -> None:
        try:
            await interaction.response.edit_message(content=text, view=None)
        except discord.HTTPException:
            pass

    # --- фоновый цикл: напоминания и кики ---

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(_LOOP_INTERVAL_SECONDS)
            try:
                await self._tick(_now())
            except Exception:
                logger.exception("Цикл staykick упал на проходе")

    async def _tick(self, now: datetime) -> None:
        for pk in await self.container.due_reminders.execute(now):
            if not self._cfg(pk.guild_id, "staykick_enabled"):
                continue  # модуль выключен на сервере
            member = self._member(pk.guild_id, pk.user_id)
            if member is not None:
                await _dm_quiet(member, await self._pick(pk.guild_id, "staykick.remind_replies"))

        for pk in await self.container.pop_due_kicks.execute(now):
            if not self._cfg(pk.guild_id, "staykick_enabled"):
                continue  # модуль выключен — не кикаем
            member = self._member(pk.guild_id, pk.user_id)
            if member is None:
                continue  # уже ушёл сам
            await _dm_quiet(member, await self._pick(pk.guild_id, "staykick.farewell_replies"))
            try:
                await member.guild.kick(member, reason="Авто-кик: выбрал «уйти»")
            except discord.HTTPException:
                logger.warning("Не удалось кикнуть по staykick", exc_info=True)

    def _member(self, guild_id: int, user_id: int) -> discord.Member | None:
        guild = self.bot.get_guild(guild_id)
        return guild.get_member(user_id) if guild is not None else None


async def _dm_quiet(member: discord.Member, text: str) -> None:
    try:
        await member.send(text)
    except discord.HTTPException:
        pass
