"""Общие фейки для тестов discord-cog'ов: исключения discord, именованный
пользователь, заготовка Interaction и Member."""

from unittest.mock import AsyncMock, MagicMock

import discord


def forbidden():
    return discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "no perms")


def not_found():
    return discord.NotFound(MagicMock(status=404, reason="Not Found"), "gone")


def http_error():
    return discord.HTTPException(MagicMock(status=500, reason="Server Error"), "boom")


class Named:
    """Объект с осмысленным str() — попадает в тексты сообщений/логов."""

    def __init__(self, uid, name="User", display=None):
        self.id = uid
        self._name = name
        self.display_name = display or name
        self.mention = f"<@{uid}>"

    def __str__(self):
        return self._name


def make_interaction(user_id=1, guild_id=10, display="Гость"):
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.user = Named(user_id, "User", display)
    interaction.channel = MagicMock()
    interaction.channel.send = AsyncMock()
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def make_member(uid=2, bot=False, name="Member"):
    member = Named(uid, name)
    member.bot = bot
    return member
