"""Гейт управляющих команд на роль-менеджера (делегирование не-админам).

Discord `default_permissions` умеет только СТАНДАРТНЫЕ права (администратор,
manage_guild…) — «произвольную роль» через него не выразить. Поэтому команда
делается видимой (с группы снят administrator-дефолт), а доступ решает сам бот:
пускаем менеджера сервера (Manage Server / админ) ЛИБО участника с заданной
ролью-менеджером. Роль в настройке задаётся строкой — ID (одни цифры) или имя."""

import discord


def can_manage_feature(member: discord.Member, role_setting: str) -> bool:
    """Участник вправе управлять фичей: Manage Server (админ входит сюда же, у
    него все права) ИЛИ имеет роль-менеджер из настройки (по ID или по имени).
    Пустая настройка = только менеджеры сервера."""
    if member.guild_permissions.manage_guild:
        return True
    setting = (role_setting or "").strip()
    if not setting:
        return False
    if setting.isdigit():
        role_id = int(setting)
        return any(r.id == role_id for r in member.roles)
    return any(r.name == setting for r in member.roles)
