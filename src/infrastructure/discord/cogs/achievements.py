"""Ког достижений: выдача по событиям + карточка-уведомление + витрина.

Разблокировка выводится из состояния (application-слой), ког лишь оркеструет:
подписан на события, которые меняют показатели, зовёт Evaluate и, если что-то
открылось, шлёт карточку в главный канал. `/achievements` заодно докручивает
ленивые ачивки (лайки/войс/диалоги — под них событий нет) и рисует витрину.
"""

import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.application.achievements.di import AchievementsContainer
from src.domain.achievements.catalog import CATALOG
from src.domain.achievements.entities import Achievement, Tier
from src.domain.events.bus import IEventBus
from src.domain.finds.events import FindClaimed
from src.domain.relationship.events import ExclusiveTransferred, RelationshipRoleChanged
from src.infrastructure.discord.channels import resolve_channel
from src.infrastructure.discord.feature_flags import (
    block_if_module_off,
    flag_on,
    require_tier,
    tier_allows,
)
from src.infrastructure.discord.interaction_ctx import guild_of
from src.infrastructure.discord.persona_phrase import PersonaPhraseMixin
from src.infrastructure.persona_service import RegistryPersona
from src.infrastructure.render.browser import CardRenderer
from src.infrastructure.render.cards import AchievementCard, achievement_card_html

logger = logging.getLogger(__name__)

_TIER_ORDER = {Tier.LEGENDARY: 0, Tier.RARE: 1, Tier.UNCOMMON: 2, Tier.COMMON: 3}
_TIER_EMOJI = {
    Tier.COMMON: "⚪",
    Tier.UNCOMMON: "🟢",
    Tier.RARE: "🔵",
    Tier.LEGENDARY: "🟡",
}


class AchievementsCog(PersonaPhraseMixin, commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        achievements: AchievementsContainer,
        settings,
        event_bus: IEventBus,
        card_renderer: CardRenderer,
        guild_settings=None,
        persona=None,
        entitlements=None,
    ):
        self.bot = bot
        self.achievements = achievements
        self.settings = settings
        self.gs = guild_settings
        self.entitlements = entitlements
        self.renderer = card_renderer
        self.persona = persona if persona is not None else RegistryPersona()
        # события, меняющие показатели → своевременная карточка. Лайки/войс/
        # диалоги событий не имеют — их добирает /achievements (Evaluate).
        event_bus.subscribe(FindClaimed, self._on_find_claimed)  # type: ignore[arg-type]
        event_bus.subscribe(RelationshipRoleChanged, self._on_role_changed)  # type: ignore[arg-type]
        event_bus.subscribe(ExclusiveTransferred, self._on_exclusive)  # type: ignore[arg-type]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await block_if_module_off(
            interaction, self.settings, self.gs, "achievements_enabled"
        ):
            return False
        return await require_tier(interaction, self.entitlements, "achievements_enabled")

    # ── реакция на события: открыть заслуженное и объявить ────────────────────

    async def _on_find_claimed(self, event: FindClaimed) -> None:
        await self._award(event.guild_id, event.user_id)

    async def _on_role_changed(self, event: RelationshipRoleChanged) -> None:
        await self._award(event.guild_id, event.user_id)

    async def _on_exclusive(self, event: ExclusiveTransferred) -> None:
        await self._award(event.guild_id, event.new_user_id)

    async def _award(self, guild_id: int, user_id: int) -> None:
        """Пересчитать ачивки участника и объявить открытые. Тихо выходит, если
        модуль выключен, участника/канала нет или рендер недоступен."""
        if guild_id == 0 or user_id == 0:
            return
        if not flag_on(self.settings, self.gs, guild_id, "achievements_enabled"):
            return
        # Ачивки — Premium. Событийный путь (нет interaction) гейтим tier_allows,
        # иначе уведомления «капали» бы на free мимо гейта /achievements.
        if not tier_allows(self.entitlements, guild_id, "achievements_enabled"):
            return
        try:
            result = await self.achievements.evaluate.execute(user_id, guild_id)
        except Exception:
            logger.exception("Ачивки: пересчёт не удался", extra={"guild_id": guild_id})
            return
        if not result.unlocked:
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = resolve_channel(
            guild,
            self.gs.get(guild_id, "main_channel_id", 0) if self.gs is not None else 0,
            self.settings.main_channel,
            fallback=False,
        )
        if channel is None:
            return
        member = guild.get_member(user_id)
        mention = member.mention if member is not None else f"<@{user_id}>"
        for achievement in result.unlocked:
            await self._announce(channel, mention, achievement)

    async def _announce(
        self, channel: discord.TextChannel, mention: str, achievement: Achievement
    ) -> None:
        text = self._p(
            channel.guild.id,
            "achievements.unlocked_announce",
            user_mention=mention,
            name=achievement.name,
        )
        png = await self._render(achievement)
        try:
            if png is not None:
                await channel.send(
                    text, file=discord.File(io.BytesIO(png), filename=f"ach_{achievement.id}.png")
                )
            else:
                await channel.send(text)  # фолбэк без карточки
        except discord.HTTPException:
            logger.warning("Ачивки: не удалось отправить уведомление", exc_info=True)

    async def _render(self, achievement: Achievement) -> bytes | None:
        try:
            card = AchievementCard(
                name=achievement.name,
                description=achievement.description,
                tier=achievement.tier.value,
                icon=achievement.icon,
            )
            html, w, h = achievement_card_html(card)
            return await self.renderer.render(html, w, h)
        except Exception:
            logger.warning(
                "Ачивки: карточка не отрисована — уведомление без картинки", exc_info=True
            )
            return None

    # ── витрина ───────────────────────────────────────────────────────────────

    @app_commands.command(name="achievements", description="Твои достижения на сервере")
    @app_commands.guild_only()
    async def achievements_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        gid = guild_of(interaction).id
        # заодно докрутить ленивые ачивки (лайки/войс/диалоги — без событий)
        result = await self.achievements.evaluate.execute(interaction.user.id, gid)
        unlocked_ids = await self.achievements.get.execute(interaction.user.id, gid)
        embed = self._build_board(gid, unlocked_ids.unlocked_ids)
        await interaction.followup.send(embed=embed, ephemeral=True)
        # если во время открытия витрины что-то анлокнулось — тихо, без спама
        # в общий канал (человек и так смотрит доску)
        _ = result

    def _build_board(self, guild_id: int, unlocked_ids: set[str]) -> discord.Embed:
        total = len(CATALOG)
        title = self._p(
            guild_id, "achievements.showcase_title", unlocked=len(unlocked_ids), total=total
        )
        embed = discord.Embed(title=title, colour=discord.Colour.blurple())
        if not unlocked_ids:
            embed.description = self._p(guild_id, "achievements.showcase_empty")
        ordered = sorted(CATALOG, key=lambda a: (_TIER_ORDER[a.tier], a.name))
        lines = []
        for a in ordered:
            got = a.id in unlocked_ids
            mark = "✅" if got else "🔒"
            emoji = _TIER_EMOJI[a.tier]
            name = f"**{a.name}**" if got else a.name
            lines.append(f"{mark} {emoji} {name} — {a.description}")
        embed.description = (embed.description + "\n\n" if embed.description else "") + "\n".join(
            lines
        )
        return embed
