"""can_manage_feature: гейт управляющих команд на менеджера сервера ИЛИ
роль-менеджера (по ID или имени)."""

from unittest.mock import MagicMock

from src.infrastructure.discord.access import can_manage_feature


def _role(rid: int, rname: str):
    r = MagicMock()
    r.id = rid
    r.name = rname  # явно: MagicMock(name=...) — зарезервированный аргумент
    return r


def _member(manage_guild=False, roles=()):
    m = MagicMock()
    m.guild_permissions.manage_guild = manage_guild
    m.roles = list(roles)
    return m


def test_manage_guild_always_allowed():
    # менеджер сервера (и админ — у него все права) проходит при любой настройке
    assert can_manage_feature(_member(manage_guild=True), "") is True
    assert can_manage_feature(_member(manage_guild=True), "любая-роль") is True


def test_empty_setting_denies_non_manager():
    assert can_manage_feature(_member(roles=[_role(1, "X")]), "") is False
    assert can_manage_feature(_member(roles=[_role(1, "X")]), "   ") is False


def test_role_by_id():
    m = _member(roles=[_role(123, "Git-куратор")])
    assert can_manage_feature(m, "123") is True
    assert can_manage_feature(m, "999") is False


def test_role_by_name():
    m = _member(roles=[_role(123, "Git-куратор")])
    assert can_manage_feature(m, "Git-куратор") is True
    assert can_manage_feature(m, "  Git-куратор  ") is True  # пробелы обрезаются
    assert can_manage_feature(m, "Другая") is False


def test_no_roles_denied():
    assert can_manage_feature(_member(roles=[]), "Git-куратор") is False
