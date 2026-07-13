"""Админ-команды /config: тюнинг настроек сервера без правки .env и пересборки.
Редактируемые ключи ограничены реестром SETTING_SPECS."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.infrastructure.guild_settings import SETTING_SPECS, GuildSettingsService

logger = logging.getLogger(__name__)

_EMBED_COLOR = 0x9B59B6


def _fmt(spec, value) -> str:
    """Человекочитаемое значение настройки."""
    if spec.kind == "channel":
        return f"<#{value}>" if value else "выключено (0)"
    if spec.kind == "bool":
        return "вкл ✅" if value else "выкл ❌"
    return f"{value}{(' ' + spec.unit) if spec.unit else ''}"


class ConfigCog(commands.Cog):
    def __init__(self, bot: commands.Bot, guild_settings: GuildSettingsService):
        self.bot = bot
        self.settings = guild_settings

    config_group = app_commands.Group(
        name="config",
        description="Настройки сервера (админ)",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True,
    )

    async def _key_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        needle = current.lower()
        return [
            app_commands.Choice(name=f"{spec.key} — {spec.label}"[:100], value=spec.key)
            for spec in SETTING_SPECS.values()
            if needle in spec.key.lower() or needle in spec.label.lower()
        ][:25]

    @config_group.command(name="list", description="Все настройки сервера и их значения")
    async def config_list(self, interaction: discord.Interaction) -> None:
        gid = interaction.guild_id
        lines = []
        for spec in SETTING_SPECS.values():
            value = self.settings.current(gid, spec.key)
            mark = "🔧" if self.settings.is_override(gid, spec.key) else "·"
            lines.append(f"{mark} **{spec.key}** — {_fmt(spec, value)}  *{spec.label}*")
        embed = discord.Embed(
            title="⚙️ Настройки сервера",
            description="\n".join(lines)[:4000],
            color=_EMBED_COLOR,
        )
        embed.set_footer(text="🔧 — переопределено на сервере · · — глобальный дефолт")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @config_group.command(name="show", description="Подробно об одной настройке")
    @app_commands.describe(key="Ключ настройки")
    @app_commands.autocomplete(key=_key_autocomplete)
    async def config_show(self, interaction: discord.Interaction, key: str) -> None:
        spec = SETTING_SPECS.get(key)
        if spec is None:
            await interaction.response.send_message(
                "Нет такой настройки. Смотри `/config list`.", ephemeral=True
            )
            return
        gid = interaction.guild_id
        current = self.settings.current(gid, key)
        default = self.settings.default(key)
        overridden = self.settings.is_override(gid, key)
        rng = ""
        if spec.kind == "int" and (spec.min is not None or spec.max is not None):
            rng = f"\n**Диапазон:** {spec.min}–{spec.max}"
        embed = discord.Embed(
            title=f"⚙️ {spec.key}",
            description=(
                f"{spec.label}\n\n"
                f"**Сейчас:** {_fmt(spec, current)}"
                + (" 🔧 (переопределено)" if overridden else " (дефолт)")
                + f"\n**Дефолт:** {_fmt(spec, default)}{rng}"
            ),
            color=_EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @config_group.command(name="set", description="Задать настройку для этого сервера")
    @app_commands.describe(key="Ключ настройки", value="Новое значение")
    @app_commands.autocomplete(key=_key_autocomplete)
    async def config_set(self, interaction: discord.Interaction, key: str, value: str) -> None:
        spec = SETTING_SPECS.get(key)
        if spec is None:
            await interaction.response.send_message(
                "Нет такой настройки. Смотри `/config list`.", ephemeral=True
            )
            return
        try:
            parsed = await self.settings.set(interaction.guild_id, key, value)
        except ValueError as exc:
            await interaction.response.send_message(
                f"Не приняла: {exc}. `{key}` — {spec.label}.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"✅ **{key}** = {_fmt(spec, parsed)} (для этого сервера).", ephemeral=True
        )
        logger.info(
            "Настройка сервера изменена",
            extra={
                "guild_id": interaction.guild_id,
                "key": key,
                "value": parsed,
                "by": interaction.user.id,
            },
        )

    @config_group.command(name="reset", description="Вернуть настройку к глобальному дефолту")
    @app_commands.describe(key="Ключ настройки")
    @app_commands.autocomplete(key=_key_autocomplete)
    async def config_reset(self, interaction: discord.Interaction, key: str) -> None:
        spec = SETTING_SPECS.get(key)
        if spec is None:
            await interaction.response.send_message(
                "Нет такой настройки. Смотри `/config list`.", ephemeral=True
            )
            return
        removed = await self.settings.reset(interaction.guild_id, key)
        default = self.settings.default(key)
        if removed:
            await interaction.response.send_message(
                f"↩️ **{key}** сброшено к дефолту: {_fmt(spec, default)}.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"**{key}** и так на дефолте: {_fmt(spec, default)}.", ephemeral=True
            )
