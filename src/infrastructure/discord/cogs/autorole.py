"""Автовыдача ролей новичку при входе на сервер.

Настройка — список id ролей `autorole_ids` в пер-серверных настройках
(редактируется на вкладке «Роли» веб-панели, хранится в guild_settings, бот
подхватывает изменения через pg_notify). Пусто = фича выключена, отдельного
тумблера не нужно.

Ограждения те же, что у выдачи ролей из панели: не трогаем @everyone, роли
интеграций (managed) и всё, что выше высшей роли бота, — Discord их всё равно не
даст выдать. Ботам-новичкам автороль не назначаем.
"""

import logging

import discord
from discord.ext import commands

from src.application.interfaces.settings_provider import ISettingsProvider

logger = logging.getLogger(__name__)


class AutoRoleCog(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: ISettingsProvider):
        self.bot = bot
        self._settings = settings

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return  # ботам автороль не выдаём
        raw_ids = self._settings.get(member.guild.id, "autorole_ids", [])
        if not raw_ids:
            return
        roles = self._resolve(member.guild, raw_ids)
        if not roles:
            return
        try:
            await member.add_roles(*roles, reason="Автороль при входе")
        except discord.Forbidden:
            logger.warning(
                "Автороль не выдана — нет права Manage Roles (или роль выше моей)",
                extra={"guild_id": member.guild.id, "user_id": member.id},
            )
        except discord.HTTPException:
            logger.exception("Автороль: ошибка выдачи", extra={"guild_id": member.guild.id})

    @staticmethod
    def _resolve(guild: discord.Guild, raw_ids: list[int]) -> list[discord.Role]:
        """id из настроек -> реально выдаваемые роли (существуют, ниже бота, не
        managed/@everyone). Невалидные молча отсеиваются: настройка могла
        устареть (роль удалили/подняли выше бота)."""
        me = guild.me
        top = me.top_role.position if me is not None else None
        roles: list[discord.Role] = []
        for rid in raw_ids:
            role = guild.get_role(int(rid))
            if role is None or role.is_default() or role.managed:
                continue
            if top is None or role.position >= top:
                continue
            roles.append(role)
        return roles
