"""/premium — что открыто на сервере и что даёт подписка (голос Попоси).

Информационная команда, доступна всем: показывает текущий тариф сервера
(из EntitlementService) и коротко — что входит во Free / Premium / Pro. Выдаёт
подписку не она, а оператор в панели (вкладка «Подписка»)."""

from datetime import UTC

import discord
from discord import app_commands
from discord.ext import commands

from src.application.interfaces.entitlements import PlanTier

_TIER_TITLE = {
    PlanTier.FREE: "☕ Free — зашла в гости",
    PlanTier.PREMIUM: "🖤 Premium — свой дом",
    PlanTier.PRO: "✂️👁🖤 Pro — сеть домов",
}

_FREE = (
    "Модерация и апелляции, базовое общение (с лимитом), музыка, "
    "кусок отношений, находки изредка, каморки (немного)."
)
_PREMIUM = (
    "Полная персона и память, secret room, отношения целиком, находки чаще, "
    "караоке и радио, /git и /steam, дайджест, ачивки-карточки, «Альбом», "
    "панель на редактирование, banwatch, больше каморок и напоминаний."
)
_PRO = "Всё из Premium + 24/7-присутствие и приоритет."


class PremiumCog(commands.Cog):
    def __init__(self, bot: commands.Bot, settings, entitlements=None):
        self.bot = bot
        self.settings = settings
        self.entitlements = entitlements

    @app_commands.command(
        name="premium", description="Тариф этого сервера и что открыто"
    )
    @app_commands.guild_only()
    async def premium(self, interaction: discord.Interaction) -> None:
        gid = interaction.guild_id or 0
        if self.entitlements is not None:
            tier, expires_at, active = self.entitlements.current(gid)
        else:
            tier, expires_at, active = PlanTier.PRO, None, False

        embed = discord.Embed(
            title=_TIER_TITLE.get(tier, "Тариф"),
            colour=0x2B2D31,
        )
        if active and expires_at is not None:
            when = expires_at.replace(tzinfo=UTC).strftime("%d.%m.%Y")
            embed.description = f"Сейчас у нас **{tier.name.title()}** — до {when}."
        elif active:
            embed.description = f"Сейчас у нас **{tier.name.title()}** — бессрочно."
        elif tier is PlanTier.FREE:
            embed.description = "Мы живём на **Free**. Уютно, но дом бывает теплее."
        else:
            embed.description = f"Сейчас у нас **{tier.name.title()}** — по умолчанию."

        embed.add_field(name="Free", value=_FREE, inline=False)
        embed.add_field(name="Premium", value=_PREMIUM, inline=False)
        embed.add_field(name="Pro", value=_PRO, inline=False)
        embed.set_footer(text="Подписку включает владелец бота в панели, вкладка «Подписка».")
        await interaction.response.send_message(embed=embed, ephemeral=True)
