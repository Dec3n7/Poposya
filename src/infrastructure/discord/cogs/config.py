"""Админ-команды /config: пер-серверный тюнинг без правки .env и пересборки.

Типизированные подкоманды с нативными пикерами Discord:
  /config channel — настройки-каналы (пикер канала; без канала = выключить);
  /config toggle  — булевы (вкл/выкл);
  /config number  — числовые (int/float);
  /config list · show · reset — обзор и сброс.
Редактируемые ключи задаёт модель GuildSettings (реестр SETTING_SPECS)."""

import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from src.infrastructure.discord.accent import accent
from src.infrastructure.guild_settings import (
    FEATURE_FLAG_KEYS,
    SETTING_SPECS,
    GuildSettingsService,
)

logger = logging.getLogger(__name__)


def _fmt(spec, value) -> str:
    """Человекочитаемое значение настройки."""
    if spec.kind == "channel":
        return f"<#{value}>" if value else "выключено (0)"
    if spec.kind == "bool":
        return "вкл ✅" if value else "выкл ❌"
    return f"{value}{(' ' + spec.unit) if spec.unit else ''}"


def _parse_int_list(text: str) -> list[int]:
    """«100, 250 300» -> [100, 250, 300]. ValueError, если не только числа."""
    parts = [p for p in re.split(r"[,\s]+", text.strip()) if p]
    return [int(p) for p in parts]


def _parse_str_list(text: str) -> list[str]:
    """Имена по одному на строку; пустые строки отбрасываются."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _parse_level_limits(text: str) -> dict[int, int]:
    """Строки «уровень: лимит» -> {уровень: лимит}. ValueError на кривой строке."""
    result: dict[int, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        level_s, sep, limit_s = line.partition(":")
        if not sep:
            raise ValueError("нужен формат «уровень: лимит»")
        result[int(level_s.strip())] = int(limit_s.strip())
    return result


class _RolesModal(discord.ui.Modal):
    """Пороги и имена ролей редактируются вместе — их связывает инвариант
    (имён = порогов + 1), поэтому одна форма и атомарное сохранение."""

    def __init__(self, cog: "ConfigCog", thresholds_default: str, names_default: str):
        super().__init__(title="Роли-статусы: пороги и имена")
        self._cog = cog
        self.thresholds = discord.ui.TextInput(
            label="Пороги очков (через запятую, по возрастанию)",
            default=thresholds_default,
            style=discord.TextStyle.short,
            max_length=400,
        )
        self.names = discord.ui.TextInput(
            label="Имена ролей (по одному на строку, = порогов + 1)",
            default=names_default,
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self.add_item(self.thresholds)
        self.add_item(self.names)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._cog._apply_roles(interaction, self.thresholds.value, self.names.value)


class _LimitsModal(discord.ui.Modal):
    """Лимиты AI-реплик в час по уровню отношений (словарь)."""

    def __init__(self, cog: "ConfigCog", limits_default: str):
        super().__init__(title="Лимиты AI-реплик по уровням")
        self._cog = cog
        self.limits = discord.ui.TextInput(
            label="Строки «уровень: лимит в час»",
            default=limits_default,
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.add_item(self.limits)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._cog._apply_limits(interaction, self.limits.value)


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

    # --- автодополнение ключей (общий фильтр + по типу) ---

    def _choices(self, current: str, kinds: set[str]) -> list[app_commands.Choice[str]]:
        needle = current.lower()
        return [
            app_commands.Choice(name=f"{spec.key} — {spec.label}"[:100], value=spec.key)
            for spec in SETTING_SPECS.values()
            if spec.kind in kinds
            and spec.key not in FEATURE_FLAG_KEYS  # тумблеры модулей — только через панель
            and (needle in spec.key.lower() or needle in spec.label.lower())
        ][:25]

    async def _key_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return self._choices(current, {"int", "float", "bool", "channel"})

    async def _channel_key_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return self._choices(current, {"channel"})

    async def _bool_key_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return self._choices(current, {"bool"})

    async def _number_key_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return self._choices(current, {"int", "float"})

    # --- общий путь записи + ответ ---

    def _resolve(self, key: str, kinds: set[str]):
        spec = SETTING_SPECS.get(key)
        if spec is None or spec.kind not in kinds:
            return None
        return spec

    async def _apply(self, interaction: discord.Interaction, spec, raw: str) -> None:
        try:
            parsed = await self.settings.set(interaction.guild_id, spec.key, raw)
        except ValueError as exc:
            await interaction.response.send_message(
                f"Не приняла: {exc}. `{spec.key}` — {spec.label}.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"✅ **{spec.key}** = {_fmt(spec, parsed)} (для этого сервера).", ephemeral=True
        )
        logger.info(
            "Настройка сервера изменена",
            extra={
                "guild_id": interaction.guild_id,
                "key": spec.key,
                "value": parsed,
                "by": interaction.user.id,
            },
        )

    # --- обзор ---

    @config_group.command(name="list", description="Все настройки сервера и их значения")
    async def config_list(self, interaction: discord.Interaction) -> None:
        gid = interaction.guild_id
        lines = []
        for spec in SETTING_SPECS.values():
            if spec.key in FEATURE_FLAG_KEYS:
                continue  # тумблеры модулей — на вкладке «Модули» панели
            value = self.settings.current(gid, spec.key)
            mark = "🔧" if self.settings.is_override(gid, spec.key) else "·"
            lines.append(f"{mark} **{spec.key}** — {_fmt(spec, value)}  *{spec.label}*")
        embed = discord.Embed(
            title="⚙️ Настройки сервера",
            description="\n".join(lines)[:4000],
            color=accent(gid),
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
        if spec.kind in ("int", "float") and (spec.min is not None or spec.max is not None):
            rng = f"\n**Диапазон:** {spec.min}–{spec.max}"
        embed = discord.Embed(
            title=f"⚙️ {spec.key}",
            description=(
                f"{spec.label}\n\n"
                f"**Сейчас:** {_fmt(spec, current)}"
                + (" 🔧 (переопределено)" if overridden else " (дефолт)")
                + f"\n**Дефолт:** {_fmt(spec, default)}{rng}"
            ),
            color=accent(gid),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- типизированная запись ---

    @config_group.command(
        name="channel", description="Задать настройку-канал (без канала — выключить)"
    )
    @app_commands.describe(key="Настройка-канал", channel="Канал; не указывай — выключить (0)")
    @app_commands.autocomplete(key=_channel_key_ac)
    async def config_channel(
        self,
        interaction: discord.Interaction,
        key: str,
        channel: discord.abc.GuildChannel | None = None,
    ) -> None:
        spec = self._resolve(key, {"channel"})
        if spec is None:
            await interaction.response.send_message(
                "Нет такой настройки-канала. Смотри `/config list`.", ephemeral=True
            )
            return
        await self._apply(interaction, spec, str(channel.id) if channel else "0")

    @config_group.command(name="toggle", description="Включить/выключить настройку")
    @app_commands.describe(key="Булева настройка", value="Вкл или выкл")
    @app_commands.autocomplete(key=_bool_key_ac)
    async def config_toggle(self, interaction: discord.Interaction, key: str, value: bool) -> None:
        spec = self._resolve(key, {"bool"})
        if spec is None:
            await interaction.response.send_message(
                "Нет такой вкл/выкл-настройки. Смотри `/config list`.", ephemeral=True
            )
            return
        await self._apply(interaction, spec, "1" if value else "0")

    @config_group.command(name="number", description="Задать числовую настройку")
    @app_commands.describe(key="Числовая настройка", value="Новое значение")
    @app_commands.autocomplete(key=_number_key_ac)
    async def config_number(self, interaction: discord.Interaction, key: str, value: float) -> None:
        spec = self._resolve(key, {"int", "float"})
        if spec is None:
            await interaction.response.send_message(
                "Нет такой числовой настройки. Смотри `/config list`.", ephemeral=True
            )
            return
        # целые ключи принимают целое: 8.0 -> "8"; дробное для int-ключа
        # осознанно уедет в парсер и вернёт понятную ошибку «нужно целое»
        raw = str(int(value)) if value.is_integer() else str(value)
        await self._apply(interaction, spec, raw)

    # --- списки/словари: роли и лимиты (через модалку) ---

    async def _apply_roles(
        self, interaction: discord.Interaction, thresholds_text: str, names_text: str
    ) -> None:
        try:
            thresholds = _parse_int_list(thresholds_text)
            names = _parse_str_list(names_text)
        except ValueError:
            await interaction.response.send_message(
                "Пороги — только целые числа через запятую/пробел.", ephemeral=True
            )
            return
        try:
            await self.settings.set_many(
                interaction.guild_id,
                {
                    "relationship_role_thresholds": thresholds,
                    "relationship_role_names": names,
                },
            )
        except ValueError as exc:
            await interaction.response.send_message(f"Не приняла: {exc}.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"✅ Роли обновлены: {len(thresholds)} порогов и {len(names)} имён "
            "(для этого сервера).",
            ephemeral=True,
        )
        logger.info(
            "Роли-статусы изменены",
            extra={"guild_id": interaction.guild_id, "by": interaction.user.id},
        )

    async def _apply_limits(self, interaction: discord.Interaction, limits_text: str) -> None:
        try:
            limits = _parse_level_limits(limits_text)
        except ValueError as exc:
            await interaction.response.send_message(f"Не приняла: {exc}.", ephemeral=True)
            return
        try:
            await self.settings.set_many(interaction.guild_id, {"ai_rate_limits_by_level": limits})
        except ValueError as exc:
            await interaction.response.send_message(f"Не приняла: {exc}.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"✅ Лимиты реплик обновлены ({len(limits)} уровней, для этого сервера).",
            ephemeral=True,
        )

    @config_group.command(name="roles", description="Пороги очков и имена ролей-статусов")
    @app_commands.describe(reset="Вернуть пороги и имена к глобальным дефолтам")
    async def config_roles(self, interaction: discord.Interaction, reset: bool = False) -> None:
        gid = interaction.guild_id
        if reset:
            await self.settings.reset(gid, "relationship_role_thresholds")
            await self.settings.reset(gid, "relationship_role_names")
            await interaction.response.send_message(
                "↩️ Пороги и имена ролей сброшены к дефолту.", ephemeral=True
            )
            return
        gs = self.settings.resolved(gid)
        thresholds_default = ", ".join(str(t) for t in gs.relationship_role_thresholds)
        names_default = "\n".join(gs.relationship_role_names)
        await interaction.response.send_modal(_RolesModal(self, thresholds_default, names_default))

    @config_group.command(name="limits", description="Лимиты AI-реплик в час по уровню отношений")
    @app_commands.describe(reset="Вернуть лимиты к глобальным дефолтам")
    async def config_limits(self, interaction: discord.Interaction, reset: bool = False) -> None:
        gid = interaction.guild_id
        if reset:
            await self.settings.reset(gid, "ai_rate_limits_by_level")
            await interaction.response.send_message(
                "↩️ Лимиты реплик сброшены к дефолту.", ephemeral=True
            )
            return
        limits = self.settings.resolved(gid).ai_rate_limits_by_level
        default = "\n".join(f"{lvl}: {lim}" for lvl, lim in sorted(limits.items()))
        await interaction.response.send_modal(_LimitsModal(self, default))

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
