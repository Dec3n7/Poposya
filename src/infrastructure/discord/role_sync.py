import logging

import discord

logger = logging.getLogger(__name__)


class RoleSyncService:
    """Создание и выдача ролей-статусов. Сверка вызывается при каждом
    начислении очков (самовосстановление после потерянных событий, ТЗ 8.5).

    Роль-«ступень 0» (`relationship_newcomer_role`) — часть того же управляемого
    набора: её держат все, у кого ещё нет статус-роли (role_index None, 0–99
    очков). Снимается тем же механизмом, что и прочие, как только человек берёт
    первую статус-роль; при угасании ниже порога — возвращается. Пусто = выкл."""

    def __init__(self, bot: discord.Client, role_names: list[str], settings_provider=None):
        self._bot = bot
        self._role_names = role_names
        self._settings = settings_provider

    def _names_for(self, guild_id: int) -> list[str]:
        """Имена ролей-статусов сервера (per-guild override или глобальный дефолт)."""
        if self._settings is not None:
            return self._settings.resolved(guild_id).relationship_role_names
        return self._role_names

    def _newcomer_for(self, guild_id: int) -> str:
        """Имя роли-«ступень 0» сервера или "" (функция выключена)."""
        if self._settings is not None:
            # getattr со значением по умолчанию: провайдер-заглушка без этого
            # поля (или старый) означает «функция выключена», а не падение
            return getattr(self._settings.resolved(guild_id), "relationship_newcomer_role", "")
        return ""

    def _managed_names(self, guild_id: int) -> list[str]:
        """Полный набор управляемых ролей: статус-роли + (опц.) роль-новичок.
        Роль-новичок держим отдельным именем, поэтому дубли (если её назвали как
        статус-роль) схлопываются в dict вызывающего — это безопасно."""
        names = list(self._names_for(guild_id))
        newcomer = self._newcomer_for(guild_id)
        if newcomer and newcomer not in names:
            names.append(newcomer)
        return names

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
        # статус-роли + роль-новичок создаём одним проходом
        for name in self._managed_names(guild.id):
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
        ровно одна нужная роль, остальные из набора снимаются.

        `role_index is None` (0–99 очков) означает «ступень 0»: желаемая роль —
        роль-новичок, если она настроена; иначе — никакой (как раньше)."""
        if not self._sync_enabled(guild.id):
            return
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return

        names = self._names_for(guild.id)
        newcomer = self._newcomer_for(guild.id)
        managed = {
            name: discord.utils.get(guild.roles, name=name)
            for name in self._managed_names(guild.id)
        }

        if role_index is not None and 0 <= role_index < len(names):
            desired = managed.get(names[role_index])
        elif newcomer:
            desired = managed.get(newcomer)  # ступень 0
        else:
            desired = None

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

    async def backfill_newcomers(self, guild: discord.Guild) -> None:
        """Разовая раздача роли-новичка всем, у кого ещё нет ни одной статус-роли.

        Нужна, чтобы «ступень 0» была и у молчунов, которые ни разу не писали (и
        потому не проходили через sync_member). Только добавляет; редкий случай
        «100+ очков, но статус-роль снята руками» самоисправится следующим
        начислением через sync_member. Вызывается на старте (on_ready)."""
        if not self._sync_enabled(guild.id):
            return
        newcomer = self._newcomer_for(guild.id)
        if not newcomer:
            return
        role = discord.utils.get(guild.roles, name=newcomer)
        if role is None:
            return
        status_names = set(self._names_for(guild.id))
        added = 0
        for member in guild.members:
            if member.bot:
                continue
            if role in member.roles:
                continue
            if any(r.name in status_names for r in member.roles):
                continue  # уже есть статус-роль — новичком не считается
            try:
                await member.add_roles(role, reason="Роль-новичок (бэкфилл)")
                added += 1
            except discord.Forbidden:
                logger.warning(
                    "Нет права выдать роль-новичок при бэкфилле",
                    extra={"guild_id": guild.id},
                )
                return  # прав нет — дальше смысла нет
            except discord.HTTPException:
                logger.warning("Не удалось выдать роль-новичок", exc_info=True)
        if added:
            logger.info("Бэкфилл роли-новичка", extra={"guild_id": guild.id, "added": added})
