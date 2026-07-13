import logging

import discord

logger = logging.getLogger(__name__)


class RoleSyncService:
    """Создание и выдача ролей-статусов. Сверка вызывается при каждом
    начислении очков (самовосстановление после потерянных событий, ТЗ 8.5)."""

    def __init__(self, bot: discord.Client, role_names: list[str]):
        self._bot = bot
        self._role_names = role_names

    async def ensure_roles(self, guild: discord.Guild) -> None:
        existing = {role.name for role in guild.roles}
        for name in self._role_names:
            if name in existing:
                continue
            try:
                await guild.create_role(name=name, reason="Роль-статус отношений")
                logger.info("Создана роль-статус", extra={"guild_id": guild.id, "role": name})
            except discord.Forbidden:
                logger.warning(
                    "Нет права Manage Roles — роли-статусы не созданы",
                    extra={"guild_id": guild.id},
                )
                return
            except discord.HTTPException:
                logger.warning("Не удалось создать роль", extra={"role": name}, exc_info=True)

    async def sync_member(self, guild: discord.Guild, user_id: int, role_index: int | None) -> None:
        """Приводит роли-статусы участника к вычисленному состоянию:
        ровно одна нужная роль, остальные из набора снимаются."""
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return

        managed = {name: discord.utils.get(guild.roles, name=name) for name in self._role_names}
        desired = (
            managed.get(self._role_names[role_index])
            if role_index is not None and 0 <= role_index < len(self._role_names)
            else None
        )

        to_remove = [
            role for name, role in managed.items()
            if role is not None and role in member.roles and role != desired
        ]
        try:
            if to_remove:
                await member.remove_roles(*to_remove, reason="Смена статуса отношений")
            if desired is not None and desired not in member.roles:
                await member.add_roles(desired, reason="Статус отношений")
        except discord.Forbidden:
            logger.warning(
                "Нет права управлять ролями участника (роль бота ниже ролей-статусов?)",
                extra={"guild_id": guild.id, "user_id": user_id},
            )
        except discord.HTTPException:
            logger.warning("Не удалось синхронизировать роли", exc_info=True)
