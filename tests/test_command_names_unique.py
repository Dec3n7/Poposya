"""Регресс: имена слеш-команд уникальны по всем когам.

Discord держит один глобальный неймспейс для слеш-команд; два кога с одинаковым
name роняют бота на старте (CommandAlreadyRegistered) — а обычные тесты когов
дёргают callback напрямую и регистрацию не проверяют. Этот тест собирает имена
из всех когов статически и падает на дубле (как было с /history: музыка +
модерация)."""

import importlib
import pkgutil

from discord import app_commands
from discord.ext import commands as commands_ext

import src.infrastructure.discord.cogs as cogs_pkg


def _all_cog_classes():
    classes = []
    for mod in pkgutil.walk_packages(cogs_pkg.__path__, cogs_pkg.__name__ + "."):
        module = importlib.import_module(mod.name)
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, commands_ext.Cog)
                and obj is not commands_ext.Cog
                and obj.__module__ == module.__name__  # определён здесь, не импортирован
            ):
                classes.append(obj)
    return classes


def test_slash_command_names_unique_across_cogs():
    seen: dict[str, str] = {}
    dups: list[tuple[str, str, str]] = []
    for cog in _all_cog_classes():
        for cmd in getattr(cog, "__cog_app_commands__", []):
            if not isinstance(cmd, app_commands.Command | app_commands.Group):
                continue  # контекстные меню — отдельный неймспейс
            if cmd.name in seen:
                dups.append((cmd.name, seen[cmd.name], cog.__name__))
            else:
                seen[cmd.name] = cog.__name__
    assert not dups, f"дублирующиеся имена слеш-команд: {dups}"
    # страховка от ложного прохода, если __cog_app_commands__ вдруг пуст
    assert len(seen) > 15, f"собрано подозрительно мало команд: {len(seen)}"
