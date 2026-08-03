"""«Кросс-серверные баны»: бот собирает баны со ВСЕХ серверов, где стоит, и даёт
модератору увидеть, что участник забанен где-то ещё.

Наружу в Discord ничего не постится: данные видит только админ — в веб-панели
(вкладка «Модерация») и через эфемерную `/checkuser` (ответ виден лишь спросившему).

Сбор идёт глобально и НЕ зависит от per-server тумблера (тумблер гасит только показ
на конкретном сервере): смысл в общей картине по всем серверам."""

import asyncio
import logging
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from src.application.banwatch.di import BanwatchContainer
from src.config import Settings
from src.domain.banwatch.entities import ServerBan
from src.infrastructure.discord.accent import accent
from src.infrastructure.discord.feature_flags import block_if_module_off
from src.infrastructure.discord.interaction_ctx import guild_of
from src.infrastructure.persona_service import RegistryPersona

logger = logging.getLogger(__name__)


def _trim(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class BanwatchCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        container: BanwatchContainer,
        settings: Settings,
        guild_settings=None,
        persona=None,
    ):
        self.bot = bot
        self.banwatch = container
        self.settings = settings
        self.gs = guild_settings
        self.persona = persona if persona is not None else RegistryPersona()
        self._backfilled = False
        self._tasks: list[asyncio.Task] = []

    def _cfg(self, guild_id: int, key: str):
        default = getattr(self.settings, key)
        return self.gs.get(guild_id, key, default) if self.gs is not None else default

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # гейтим только показ (/checkuser) — сбор банов идёт всегда
        return await block_if_module_off(interaction, self.settings, self.gs, "banwatch_enabled")

    def cog_unload(self) -> None:  # type: ignore[override]  # discord.py допускает и sync
        for task in self._tasks:
            task.cancel()

    # --- сбор банов (глобально, без per-server гейта) ---

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.abc.User) -> None:
        reason = ""
        try:
            entry = await guild.fetch_ban(user)
            reason = entry.reason or ""
        except discord.HTTPException:
            pass  # нет прав/не нашли причину — запишем бан без неё
        try:
            await self.banwatch.record_ban.execute(
                ServerBan(
                    user_id=user.id,
                    guild_id=guild.id,
                    guild_name=guild.name,
                    reason=reason,
                    banned_at=datetime.now(UTC),
                )
            )
        except Exception:
            logger.exception("Не удалось записать бан в зеркало")

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.abc.User) -> None:
        try:
            await self.banwatch.remove_ban.execute(guild.id, user.id)
        except Exception:
            logger.exception("Не удалось снять бан из зеркала")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._backfilled:
            return
        self._backfilled = True
        self._tasks.append(asyncio.create_task(self._backfill_all()))

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        self._tasks.append(asyncio.create_task(self._backfill_guild(guild)))

    async def _backfill_all(self) -> None:
        # последовательно и щадяще — не хотим лавины запросов к Discord на старте
        guilds = list(self.bot.guilds)
        for guild in guilds:
            await self._backfill_guild(guild)
            await asyncio.sleep(1)
        logger.info("Кросс-баны: бэкфилл завершён (%d серверов)", len(guilds))

    async def _backfill_guild(self, guild: discord.Guild) -> None:
        bans: list[ServerBan] = []
        try:
            async for entry in guild.bans(limit=None):
                bans.append(
                    ServerBan(
                        user_id=entry.user.id,
                        guild_id=guild.id,
                        guild_name=guild.name,
                        reason=entry.reason or "",
                        banned_at=None,  # исторические баны — время неизвестно
                    )
                )
        except discord.Forbidden:
            logger.warning("Нет права Ban Members — баны сервера недоступны",
                           extra={"guild_id": guild.id})
            return
        except discord.HTTPException:
            logger.warning("Не удалось прочитать баны сервера", exc_info=True,
                           extra={"guild_id": guild.id})
            return
        try:
            await self.banwatch.sync_guild.execute(guild.id, bans)
        except Exception:
            logger.exception("Не удалось синхронизировать баны сервера")

    # --- /checkuser (эфемерно, только для модератора) ---

    @app_commands.command(
        name="checkuser",
        description="Кросс-серверная история банов участника (видно только вам)",
    )
    @app_commands.describe(user="Кого проверить")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def checkuser(self, interaction: discord.Interaction, user: discord.User) -> None:
        await interaction.response.defer(ephemeral=True)
        report = await self.banwatch.check_user.execute(user.id, guild_of(interaction).id)
        if report.count == 0:
            await interaction.followup.send(
                f"На серверах бота {user.mention} нигде не забанен.", ephemeral=True
            )
            return
        threshold = int(self._cfg(guild_of(interaction).id, "banwatch_threshold"))
        over = " — порог пройден ⚠️" if report.count >= threshold else ""
        embed = discord.Embed(
            title=_trim(f"Бан-история: {user}", 240),
            description=f"Забанен на **{report.count}** сервере(ах) бота{over}.",
            color=accent(interaction.guild_id),
        )
        for rec in report.records[:15]:
            when = (
                f"<t:{int(rec.banned_at.timestamp())}:D>" if rec.banned_at else "дата неизвестна"
            )
            # причину бана чужого сервера не показываем (см. _records_json в
            # api/routers/moderation.py) — только где и когда
            embed.add_field(
                name=_trim(rec.guild_name or f"Сервер {rec.guild_id}", 200),
                value=when,
                inline=False,
            )
        embed.set_footer(text="Виден только вам · в Discord не публикуется")
        await interaction.followup.send(embed=embed, ephemeral=True)
