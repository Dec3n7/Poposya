"""/premium — что открыто на сервере и что даёт подписка (голос Попоси).

Информационная команда, доступна всем: показывает текущий тариф сервера
(из EntitlementService) и что входит во Free / Premium / Pro — картинкой-карточкой
(HTML→PNG), с фолбэком на текстовый эмбед, если рендерер недоступен. Выдаёт
подписку не она, а оператор в панели (вкладка «Подписка»)."""

import io
import logging
from datetime import UTC

import discord
from discord import app_commands
from discord.ext import commands

from src.application.interfaces.entitlements import PlanTier
from src.infrastructure.premium_keys.service import RedeemOutcome, RedeemResult
from src.infrastructure.render.cards import premium_card_html

logger = logging.getLogger(__name__)

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
    def __init__(
        self, bot: commands.Bot, settings, entitlements=None, card_renderer=None, premium_keys=None
    ):
        self.bot = bot
        self.settings = settings
        self.entitlements = entitlements
        self.renderer = card_renderer  # None (тесты/нет браузера) → эмбед-фолбэк
        self.premium_keys = premium_keys  # PremiumKeyService | None (фича выключена)

    def _tier_of(self, gid: int):
        if self.entitlements is not None:
            return self.entitlements.current(gid)
        return PlanTier.PRO, None, False

    @staticmethod
    def _status(tier: PlanTier, expires_at, active: bool) -> str:
        if active and expires_at is not None:
            when = expires_at.replace(tzinfo=UTC).strftime("%d.%m.%Y")
            return f"Сейчас у нас **{tier.name.title()}** — до {when}."
        if active:
            return f"Сейчас у нас **{tier.name.title()}** — бессрочно."
        if tier is PlanTier.FREE:
            return "Мы живём на **Free**. Уютно, но дом бывает теплее."
        return f"Сейчас у нас **{tier.name.title()}** — по умолчанию."

    async def _render(self, tier: PlanTier) -> bytes | None:
        if self.renderer is None:
            return None
        try:
            html, w, h = premium_card_html(tier.name.lower())
            return await self.renderer.render(html, w, h)
        except Exception:
            logger.warning("Карточка /premium не отрисована — фолбэк на эмбед", exc_info=True)
            return None

    def _embed(self, tier: PlanTier, expires_at, active: bool) -> discord.Embed:
        embed = discord.Embed(title=_TIER_TITLE.get(tier, "Тариф"), colour=0x2B2D31)
        embed.description = self._status(tier, expires_at, active)
        embed.add_field(name="Free", value=_FREE, inline=False)
        embed.add_field(name="Premium", value=_PREMIUM, inline=False)
        embed.add_field(name="Pro", value=_PRO, inline=False)
        embed.set_footer(text="Подписку включает владелец бота в панели, вкладка «Подписка».")
        return embed

    @app_commands.command(name="premium", description="Тариф этого сервера и что открыто")
    @app_commands.guild_only()
    async def premium(self, interaction: discord.Interaction) -> None:
        gid = interaction.guild_id or 0
        tier, expires_at, active = self._tier_of(gid)
        await interaction.response.defer(ephemeral=True)
        png = await self._render(tier)
        if png is not None:
            await interaction.followup.send(
                content=self._status(tier, expires_at, active),
                file=discord.File(io.BytesIO(png), filename="premium.png"),
            )
            return
        await interaction.followup.send(embed=self._embed(tier, expires_at, active))

    @staticmethod
    def _redeem_message(res: RedeemResult) -> str:
        """Ответ на активацию голосом Попоси (ephemeral)."""
        until = ""
        if res.expires_at is not None:
            until = f" до {res.expires_at.replace(tzinfo=UTC).strftime('%d.%m.%Y')}"
        tier = res.tier.name.title() if res.tier else "Premium"
        match res.outcome:
            case RedeemOutcome.OK:
                return f"Готово — включила **{tier}**{until}. Устраивайся поудобнее. 🖤"
            case RedeemOutcome.EXTENDED:
                return f"Продлила **{tier}**{until}. Никуда не расходимся."
            case RedeemOutcome.FULL:
                return f"Этот ключ уже разобрали — все {res.seats_total} слот(а/ов) заняты."
            case RedeemOutcome.REVOKED:
                return "Этот ключ отозвали. Напиши туда, где брал(а)."
            case RedeemOutcome.EXPIRED:
                return "Ключ просрочен — его не выкупили вовремя."
            case RedeemOutcome.RATE_LIMITED:
                return "Слишком много попыток подряд. Выдохни и попробуй через час."
            case _:
                return "Не узнаю этот ключ. Проверь, не закралась ли опечатка."

    @app_commands.command(name="activate", description="Активировать ключ Premium/Pro")
    @app_commands.guild_only()
    @app_commands.describe(key="Лицензионный ключ (POPO-…)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def activate(self, interaction: discord.Interaction, key: str) -> None:
        """Активация ключа на этом сервере. Гейт — Manage Guild (§5): включать
        подписку может админ, а не любой участник. Ответ ephemeral: ключ и итог не
        уходят в канал."""
        await interaction.response.defer(ephemeral=True)
        if self.premium_keys is None or not self.premium_keys.enabled:
            await interaction.followup.send("Активация ключей сейчас недоступна.", ephemeral=True)
            return
        gid = interaction.guild_id or 0
        res = await self.premium_keys.redeem(key.strip(), gid, interaction.user.id)
        await interaction.followup.send(self._redeem_message(res), ephemeral=True)
