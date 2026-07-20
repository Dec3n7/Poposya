"""Общий хелпер пер-серверных фича-флагов для когов.

Флаг — обычная bool-настройка (`GuildSettings`); значение берётся из
переопределений сервера, иначе глобальный дефолт. Отсутствующий флаг (тест-
заглушки без поля) считаем включённым."""

import discord


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
