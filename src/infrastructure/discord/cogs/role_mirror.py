"""Зеркало ролей: бот держит копию ролей каждого сервера в БД, чтобы веб-панель
читала их и иерархию без обращения к Discord-шлюзу.

Бэкфилл на старте (`on_ready`, один раз) и при входе на новый сервер; далее —
gateway-события create/update/delete. Позиция высшей роли бота (`guild.me`)
кладётся в мету при каждом изменении: она может сдвинуться, когда двигают роли,
а панель по ней рисует границу «что боту доступно».

Это плумбинг, а не пользовательский модуль — тумблером не гейтим: зеркало нужно
панели всегда, а само по себе оно ничего в Discord не меняет (только читает)."""

import logging

import discord
from discord.ext import commands

from src.application.roles.di import RolesContainer
from src.domain.roles.entities import GuildRole

logger = logging.getLogger(__name__)


def _to_entity(guild_id: int, role: discord.Role) -> GuildRole:
    return GuildRole(
        guild_id=guild_id,
        role_id=role.id,
        name=role.name,
        color=role.color.value,
        hoist=role.hoist,
        mentionable=role.mentionable,
        position=role.position,
        managed=role.managed,
        permissions=role.permissions.value,
    )


class RoleMirrorCog(commands.Cog):
    def __init__(self, bot: commands.Bot, roles: RolesContainer):
        self.bot = bot
        self.roles = roles
        # on_ready повторяется при реконнектах — бэкфилл делаем один раз
        self._synced = False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._synced:
            return
        self._synced = True
        for guild in self.bot.guilds:
            try:
                await self._backfill(guild)
            except Exception:
                logger.exception("Бэкфилл зеркала ролей упал", extra={"guild_id": guild.id})

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._backfill(guild)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        await self._upsert(role)

    @commands.Cog.listener()
    async def on_guild_role_update(self, _before: discord.Role, after: discord.Role) -> None:
        # событие приходит на каждую роль, чья позиция сдвинулась при перестановке —
        # так зеркало и порядок остаются свежими
        await self._upsert(after)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        guild = role.guild
        bot_id, bot_top = self._bot_bounds(guild)
        await self.roles.delete_role.execute(guild.id, role.id, bot_id, bot_top)

    async def _backfill(self, guild: discord.Guild) -> None:
        roles = [_to_entity(guild.id, r) for r in guild.roles]
        bot_id, bot_top = self._bot_bounds(guild)
        await self.roles.sync_guild.execute(guild.id, roles, bot_id, bot_top)
        logger.info("Зеркало ролей синхронизировано", extra={"guild_id": guild.id, "roles": len(roles)})

    async def _upsert(self, role: discord.Role) -> None:
        guild = role.guild
        bot_id, bot_top = self._bot_bounds(guild)
        await self.roles.upsert_role.execute(_to_entity(guild.id, role), bot_id, bot_top)

    @staticmethod
    def _bot_bounds(guild: discord.Guild) -> tuple[int, int]:
        """(id бота, позиция его высшей роли). guild.me есть после on_ready;
        top_role всегда существует (как минимум @everyone)."""
        me = guild.me
        if me is None:  # крайне редко: кэш ещё не прогрет
            return 0, 0
        return me.id, me.top_role.position
