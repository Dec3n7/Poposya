"""Общий хелпер пер-серверных фича-флагов для когов.

Флаг — обычная bool-настройка (`GuildSettings`); значение берётся из
переопределений сервера, иначе глобальный дефолт. Отсутствующий флаг (тест-
заглушки без поля) считаем включённым."""

import discord

from src.application.guild_config.schema import MODULE_MIN_TIER


def flag_on(settings, gs, guild_id: int, key: str) -> bool:
    default = getattr(settings, key, True)
    value = gs.get(guild_id, key, default) if gs is not None else default
    return bool(value)


async def block_if_module_off(
    interaction: discord.Interaction, settings, gs, master_key: str
) -> bool:
    """Для cog.interaction_check: True — команду можно выполнять; False (и
    эфемерное сообщение) — модуль выключен на этом сервере."""
    if interaction.guild_id is None or flag_on(settings, gs, interaction.guild_id, master_key):
        return True
    try:
        await interaction.response.send_message(
            "Этот модуль выключен на сервере. Включить — на панели, вкладка «Модули».",
            ephemeral=True,
        )
    except discord.HTTPException:
        pass
    return False


def tier_allows(entitlements, guild_id: int | None, master_key: str) -> bool:
    """Достаточен ли тариф гильдии для модуля — БЕЗ взаимодействия (для событий:
    альбом, staykick и т.п., где нет `interaction`, чтобы ответить). Free-модуль
    (нет в `MODULE_MIN_TIER`), неподключённый провайдер (`None`) или отсутствие
    гильдии -> True (пропускаем)."""
    min_tier = MODULE_MIN_TIER.get(master_key)
    if min_tier is None or entitlements is None or guild_id is None:
        return True
    return entitlements.tier(guild_id) >= min_tier


async def require_tier(interaction: discord.Interaction, entitlements, master_key: str) -> bool:
    """Гейт по тарифу для слэш-команд. True — тариф достаточен (или фича free /
    провайдер не подключён); False (и эфемерное сообщение) — нужен апгрейд.
    Ставится рядом с `block_if_module_off` в `interaction_check` Premium-кога.
    См. docs/plans/monetization-prep.md (Prep 5)."""
    if tier_allows(entitlements, interaction.guild_id, master_key):
        return True
    try:
        await interaction.response.send_message(
            "Эта возможность доступна на платном тарифе. Подробнее — команда «/premium».",
            ephemeral=True,
        )
    except discord.HTTPException:
        pass
    return False
