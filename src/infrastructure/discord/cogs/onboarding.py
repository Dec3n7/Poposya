"""Онбординг: когда бота добавляют на сервер, Попося представляется и подсказывает,
с чего начать настройку.

Публичный канал: пишем в `system_channel`, а если его нет или туда нельзя писать —
в первый текстовый канал, куда у бота есть право отправлять. Если писать некуда —
молча выходим (без спама в ЛС). Голос — каталог персоны (`onboarding.*`),
структура — здесь. Ссылка на панель показывается только если задан публичный URL."""

import logging

import discord
from discord.ext import commands

from src.config import Settings
from src.infrastructure.discord.accent import accent
from src.infrastructure.persona_service import RegistryPersona

logger = logging.getLogger(__name__)


class OnboardingCog(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings, persona=None):
        self.bot = bot
        self.settings = settings
        self.persona = persona if persona is not None else RegistryPersona()

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        channel = self._target_channel(guild)
        if channel is None:
            logger.info(
                "Онбординг: нет канала, куда написать приветствие",
                extra={"guild_id": guild.id},
            )
            return
        try:
            await channel.send(
                embed=await self._build_embed(guild),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            logger.info(
                "Онбординг: приветствие отправлено",
                extra={"guild_id": guild.id, "channel_id": channel.id},
            )
        except discord.HTTPException:
            logger.warning(
                "Онбординг: не удалось отправить приветствие",
                exc_info=True,
                extra={"guild_id": guild.id},
            )

    def _target_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        """system_channel, если туда можно писать; иначе первый пригодный текстовый."""
        me = guild.me
        if me is None:
            return None
        system = guild.system_channel
        if system is not None and system.permissions_for(me).send_messages:
            return system
        for channel in guild.text_channels:
            perms = channel.permissions_for(me)
            if perms.view_channel and perms.send_messages:
                return channel
        return None

    async def _build_embed(self, guild: discord.Guild) -> discord.Embed:
        greeting = await self.persona.render_block(guild.id, "onboarding.greeting", None) or ""
        about = str(self.persona.phrase(guild.id, "onboarding.about"))
        embed = discord.Embed(
            title="Попося на связи",
            description=f"{greeting}\n\n{about}".strip(),
            color=accent(guild.id),
        )
        setup = (
            "Открой **`/config`** — там пороги, каналы, роли и тумблеры модулей.\n"
            "Многие функции (приветствия, киноклуб, находки, музыка) заработают, "
            "когда укажешь для них каналы."
        )
        if self.settings.web_public_url:
            setup += f"\n\nПанель управления: {self.settings.web_public_url}"
        embed.add_field(name="🛠 Как настроить", value=setup, inline=False)
        embed.add_field(
            name="🔒 Приватность",
            value="Любой участник может удалить свои данные командой `/forgetme`.",
            inline=False,
        )
        embed.set_footer(text="✂️👁🖤")
        return embed
