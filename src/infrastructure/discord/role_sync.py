import logging

import discord

logger = logging.getLogger(__name__)


class RoleSyncService:
    """Создание и выдача ролей-статусов. Сверка вызывается при каждом
    начислении очков (самовосстановление после потерянных событий, ТЗ 8.5)."""

    def __init__(self, bot: discord.Client, role_names: list[str], settings_provider=None):
        self._bot = bot
        self._role_names = role_names
        self._settings = settings_provider

    def _names_for(self, guild_id: int) -> list[str]:
        """Имена ролей-статусов сервера (per-guild override или глобальный дефолт)."""
        if self._settings is not None:
            return self._settings.resolved(guild_id).relationship_role_names
        return self._role_names

    def _sync_enabled(self, guild_id: int) -> bool:
        """Физическая выдача Discord-ролей включена на сервере: модуль «Отношения»
        (мастер) И подфлаг «Выдача ролей». Гейт здесь — единая точка для всех, кто
        дёргает роли (relationship, ai_chat, activity). Без провайдера настроек
        (тест-заглушки) — считаем включённым."""
        if self._settings is None:
            return True
        resolved = self._settings.resolved(guild_id)
        return bool(
            getattr(resolved, "relationship_enabled", True)
            and getattr(resolved, "relationship_role_sync", True)
        )

    async def ensure_roles(self, guild: discord.Guild) -> None:
        if not self._sync_enabled(guild.id):
            return
        existing = {role.name for role in guild.roles}
        for name in self._names_for(guild.id):
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
        if not self._sync_enabled(guild.id):
            return
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return

        names = self._names_for(guild.id)
        managed = {name: discord.utils.get(guild.roles, name=name) for name in names}
        desired = (
            managed.get(names[role_index])
            if role_index is not None and 0 <= role_index < len(names)
            else None
        )

        to_remove = [
            role
            for name, role in managed.items()
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
