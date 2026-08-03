"""Сужение опциональных полей Interaction для guild-only команд.

`interaction.guild` и `interaction.user` статически опциональны/широки (Discord
допускает ЛС), но слэш-команды когов помечены `@app_commands.guild_only()` —
Discord не пускает их в личку, поэтому в теле команды guild и Member
гарантированы.

`guild_of` проверяет `is None` (мок-безопасно: тест-заглушки дают не-None guild)
и громко падает, если `guild_only` однажды снимут. `member_of` сузить проверкой
`isinstance` нельзя — duck-typed тест-моки не наследуют `discord.Member`, и
проверка отсекла бы их; поэтому cast: `guild_only` гарантирует тип в рантайме, а
cast — no-op, моки не ломает."""

from typing import cast

import discord


def guild_of(interaction: discord.Interaction) -> discord.Guild:
    guild = interaction.guild
    if guild is None:  # guild_only гарантирует наличие сервера
        raise RuntimeError("guild-only команда вызвана вне сервера")
    return guild


def member_of(interaction: discord.Interaction) -> discord.Member:
    return cast(discord.Member, interaction.user)
