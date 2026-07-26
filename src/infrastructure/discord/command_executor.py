"""Исполнитель команд моста на стороне бота: единственное место, где команда
панели превращается в реальное Discord-действие.

Модерация (бан/разбан/мут/анмут) и управление музыкой (пауза/продолжить/
пропустить/стоп). Всё исполнимо по id: бан/разбан через discord.Object, мут
через get/fetch_member, музыка — через живой MusicPlayerService гильдии.
Ожидаемые провалы (нет прав, участник/сессия не найдены) кидаются как
CommandError — панель покажет их админу.
"""

import asyncio
import base64
import binascii
import ipaddress
import logging
import socket
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import aiohttp
import discord
from discord.ext import commands as discord_commands

from src.application.moderation.di import ModerationContainer
from src.infrastructure.commands.bridge import Command, CommandError

logger = logging.getLogger(__name__)

# аватар/баннер Discord принимает картинкой; качаем по URL из панели. Лимит и
# проверка типа — чтобы не тянуть гигантский/непонятный файл.
_IMAGE_MAX_BYTES = 8 * 1024 * 1024


def _is_public_ip(ip: str) -> bool:
    """Адрес НЕ ведёт во внутреннюю сеть? loopback/private/link-local/reserved/
    multicast/unspecified считаем небезопасными (SSRF во внутреннюю сеть)."""
    try:
        addr: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    # ::ffff:10.0.0.1 и подобное — проверяем встроенный IPv4-адрес
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    # not is_global ловит и то, что не покрыто явными флагами (напр. CGNAT
    # 100.64.0.0/10, TEST-NET) — «глобально маршрутизируемый?» и есть критерий.
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
        or not addr.is_global
    )


async def _assert_public_url(url: str) -> None:
    """SSRF-щит: хост URL не должен резолвиться во внутреннюю сеть. Проверяем ВСЕ
    адреса при резолве — отсекаются и literal-IP (127.0.0.1, 169.254.169.254,
    10.x, [::1]), и обфускация (http://2130706433/), и домены, указывающие
    внутрь. Остаточный риск DNS-rebinding (повторный резолв на connect вернёт
    другой адрес) для «админ зондирует внутреннюю сеть» приемлем."""
    host = urlparse(url).hostname
    if not host:
        raise CommandError("В URL картинки нет хоста.")
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise CommandError("Не удалось разрешить адрес картинки.") from exc
    if not infos or not all(_is_public_ip(info[4][0]) for info in infos):
        raise CommandError("Адрес картинки ведёт во внутреннюю сеть — запрещено.")


async def _download_image(url: str) -> bytes:
    if not url.startswith(("http://", "https://")):
        raise CommandError("URL картинки должен начинаться с http(s)://")
    await _assert_public_url(url)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    raise CommandError(f"Не удалось скачать картинку (HTTP {resp.status}).")
                ctype = resp.headers.get("Content-Type", "")
                if not ctype.startswith("image/"):
                    raise CommandError("По ссылке не картинка.")
                data = await resp.content.read(_IMAGE_MAX_BYTES + 1)
                if len(data) > _IMAGE_MAX_BYTES:
                    raise CommandError("Картинка больше 8 МБ.")
                return data
    except aiohttp.ClientError as exc:
        raise CommandError(f"Ошибка загрузки картинки: {exc}") from exc


def _decode_data_url(data_url: str) -> bytes:
    """data:image/...;base64,XXXX -> байты. Загруженный+обрезанный в панели аватар."""
    b64 = data_url.split(",", 1)[1] if data_url.startswith("data:") else data_url
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CommandError("Битые данные загруженной картинки.") from exc
    if len(raw) > _IMAGE_MAX_BYTES:
        raise CommandError("Картинка больше 8 МБ.")
    return raw


class DiscordCommandExecutor:
    def __init__(self, bot: discord_commands.Bot, moderation: ModerationContainer):
        self._bot = bot
        self._moderation = moderation

    async def execute(self, command: Command) -> str:
        guild = self._bot.get_guild(command.guild_id)
        if guild is None:
            raise CommandError("Бот не на этом сервере.")
        handler = _HANDLERS.get(command.command_type)
        if handler is None:
            raise CommandError(f"Неизвестная команда: {command.command_type}")
        return await handler(self, guild, command)

    # --- модерация ---

    async def _tempban(self, guild: discord.Guild, command: Command) -> str:
        p = command.payload
        user_id = int(p["user_id"])
        minutes = int(p["minutes"])
        reason = str(p.get("reason") or "без причины")
        try:
            await guild.ban(
                discord.Object(id=user_id),
                reason=f"{reason} (панель, до {minutes} мин)",
                delete_message_seconds=0,
            )
        except discord.Forbidden as exc:
            raise CommandError("Нет права Ban Members (или роль участника выше моей).") from exc
        expires_at = await self._moderation.temp_ban.execute(
            user_id, guild.id, command.requested_by, reason, minutes, datetime.now(UTC)
        )
        return f"Забанен до {expires_at.strftime('%d.%m.%Y %H:%M UTC')}."

    async def _unban(self, guild: discord.Guild, command: Command) -> str:
        user_id = int(command.payload["user_id"])
        try:
            await guild.unban(discord.Object(id=user_id), reason="Досрочно из панели")
        except discord.NotFound as exc:
            raise CommandError("Этот пользователь не в бане.") from exc
        except discord.Forbidden as exc:
            raise CommandError("Нет права Ban Members.") from exc
        await self._moderation.remove_ban.execute(user_id, guild.id)
        return "Разбанен."

    async def _member(self, guild: discord.Guild, user_id: int) -> discord.Member:
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.NotFound as exc:
            raise CommandError("Участник не найден на сервере.") from exc

    async def _mute(self, guild: discord.Guild, command: Command) -> str:
        p = command.payload
        minutes = int(p["minutes"])
        reason = str(p.get("reason") or "без причины")
        member = await self._member(guild, int(p["user_id"]))
        try:
            await member.timeout(timedelta(minutes=minutes), reason=f"{reason} (панель)")
        except discord.Forbidden as exc:
            raise CommandError("Нет права Timeout Members (или роль участника выше моей).") from exc
        return f"Замучен на {minutes} мин."

    async def _unmute(self, guild: discord.Guild, command: Command) -> str:
        member = await self._member(guild, int(command.payload["user_id"]))
        try:
            await member.timeout(None, reason="Снято из панели")
        except discord.Forbidden as exc:
            raise CommandError("Нет права Timeout Members.") from exc
        return "Мут снят."

    # --- музыка ---

    def _player(self, guild_id: int):
        cog = self._bot.get_cog("MusicCog")
        service = getattr(cog, "service", None)
        if service is None:
            raise CommandError("Музыкальный модуль недоступен.")
        player = service.get_player(guild_id)
        if player is None:
            raise CommandError("Сейчас ничего не играет.")
        return service, player

    async def _pause(self, guild: discord.Guild, _command: Command) -> str:
        _service, player = self._player(guild.id)
        if player.is_paused:
            return "Уже на паузе."
        await player.toggle_pause()
        return "Пауза."

    async def _resume(self, guild: discord.Guild, _command: Command) -> str:
        _service, player = self._player(guild.id)
        if not player.is_paused:
            return "Уже играет."
        await player.toggle_pause()
        return "Продолжаю."

    async def _skip(self, guild: discord.Guild, _command: Command) -> str:
        _service, player = self._player(guild.id)
        await player.skip()
        return "Пропущено."

    async def _stop(self, guild: discord.Guild, _command: Command) -> str:
        service, _player = self._player(guild.id)
        await service.cleanup(guild.id, "⏹️ Остановлено из панели.")
        return "Остановлено."

    # --- профиль бота на сервере (ник/аватар/баннер) ---

    async def _profile_apply(self, guild: discord.Guild, command: Command) -> str:
        """Применяет пер-серверный профиль бота: guild.me.edit(nick/avatar/banner).
        Пустое значение = сброс к глобальному (None). Значения приходят в payload."""
        p = command.payload
        kwargs: dict = {}
        if "nick" in p:
            kwargs["nick"] = str(p["nick"]).strip() or None
        # загруженный аватар (base64) приоритетнее URL
        avatar_data = str(p.get("avatar_data") or "").strip()
        if avatar_data:
            kwargs["avatar"] = _decode_data_url(avatar_data)
        elif "avatar_url" in p:
            url = str(p["avatar_url"]).strip()
            kwargs["avatar"] = await _download_image(url) if url else None
        # загруженный баннер (base64) приоритетнее URL
        banner_data = str(p.get("banner_data") or "").strip()
        if banner_data:
            kwargs["banner"] = _decode_data_url(banner_data)
        elif "banner_url" in p:
            url = str(p["banner_url"]).strip()
            kwargs["banner"] = await _download_image(url) if url else None
        if not kwargs:
            return "Нечего менять."
        try:
            await guild.me.edit(**kwargs)
        except discord.Forbidden as exc:
            raise CommandError("Нет прав на смену профиля (нужно Change Nickname).") from exc
        except discord.HTTPException as exc:
            # Discord может отклонить аватар/баннер (формат/размер/недоступно приложению)
            raise CommandError(f"Discord отклонил профиль: {exc.text or exc}") from exc
        parts = [k for k in ("nick", "avatar", "banner") if k in kwargs]
        return "Профиль обновлён: " + ", ".join(parts) + "."

    # --- роли: выдача/снятие участнику ---

    def _manageable_role(self, guild: discord.Guild, role_id: int) -> discord.Role:
        """Роль, которой боту можно управлять. Проверки повторяют флаг editable
        панели, но настоящая граница — здесь: панель отдельный процесс и доверять
        её вводу нельзя."""
        role = guild.get_role(role_id)
        if role is None:
            raise CommandError("Роль не найдена.")
        if role.is_default():
            raise CommandError("@everyone нельзя выдавать или снимать.")
        if role.managed:
            raise CommandError("Это роль интеграции/бустов — Discord не даёт ею управлять.")
        if role >= guild.me.top_role:
            raise CommandError("Эта роль выше моей — не могу ею управлять.")
        return role

    async def _role_assign(self, guild: discord.Guild, command: Command) -> str:
        p = command.payload
        role = self._manageable_role(guild, int(p["role_id"]))
        member = await self._member(guild, int(p["user_id"]))
        if role in member.roles:
            return f"Роль «{role.name}» уже есть."
        try:
            await member.add_roles(role, reason="Панель: выдача роли")
        except discord.Forbidden as exc:
            raise CommandError("Нет права Manage Roles (или роль выше моей).") from exc
        return f"Выдал роль «{role.name}»."

    async def _role_unassign(self, guild: discord.Guild, command: Command) -> str:
        p = command.payload
        role = self._manageable_role(guild, int(p["role_id"]))
        member = await self._member(guild, int(p["user_id"]))
        if role not in member.roles:
            return f"Роли «{role.name}» и так нет."
        try:
            await member.remove_roles(role, reason="Панель: снятие роли")
        except discord.Forbidden as exc:
            raise CommandError("Нет права Manage Roles (или роль выше моей).") from exc
        return f"Снял роль «{role.name}»."

    # --- роли: CRUD и порядок ---

    @staticmethod
    def _role_name(raw: object) -> str:
        name = str(raw or "").strip()
        if not name:
            raise CommandError("Имя роли не может быть пустым.")
        if len(name) > 100:
            raise CommandError("Имя роли длиннее 100 символов.")
        return name

    @staticmethod
    def _role_colour(raw: object) -> discord.Colour:
        # пусто/None/0 -> «без цвета» (default). Иначе int 0..0xFFFFFF.
        if raw is None or raw == "":
            return discord.Colour.default()
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise CommandError("Цвет должен быть числом.") from exc
        if not 0 <= value <= 0xFFFFFF:
            raise CommandError("Цвет вне диапазона.")
        return discord.Colour(value)

    async def _role_create(self, guild: discord.Guild, command: Command) -> str:
        # права роли НЕ трогаем (этап 2) — новая роль родится без прав, у самого
        # низа иерархии (position 1), значит заведомо ниже бота и редактируема.
        p = command.payload
        try:
            role = await guild.create_role(
                name=self._role_name(p.get("name")),
                colour=self._role_colour(p.get("color")),
                hoist=bool(p.get("hoist")),
                mentionable=bool(p.get("mentionable")),
                reason="Панель: создание роли",
            )
        except discord.Forbidden as exc:
            raise CommandError("Нет права Manage Roles.") from exc
        return f"Создал роль «{role.name}»."

    async def _role_edit(self, guild: discord.Guild, command: Command) -> str:
        p = command.payload
        role = self._manageable_role(guild, int(p["role_id"]))
        # только присланные поля; permissions намеренно недоступны (этап 2)
        kwargs: dict = {}
        if "name" in p:
            kwargs["name"] = self._role_name(p["name"])
        if "color" in p:
            kwargs["colour"] = self._role_colour(p["color"])
        if "hoist" in p:
            kwargs["hoist"] = bool(p["hoist"])
        if "mentionable" in p:
            kwargs["mentionable"] = bool(p["mentionable"])
        if not kwargs:
            return "Нечего менять."
        try:
            await role.edit(reason="Панель: изменение роли", **kwargs)
        except discord.Forbidden as exc:
            raise CommandError("Нет права Manage Roles (или роль выше моей).") from exc
        return f"Обновил роль «{role.name}»."

    async def _role_delete(self, guild: discord.Guild, command: Command) -> str:
        role = self._manageable_role(guild, int(command.payload["role_id"]))
        name = role.name
        try:
            await role.delete(reason="Панель: удаление роли")
        except discord.Forbidden as exc:
            raise CommandError("Нет права Manage Roles (или роль выше моей).") from exc
        return f"Удалил роль «{name}»."

    async def _role_reorder(self, guild: discord.Guild, command: Command) -> str:
        """Переставить редактируемые роли. order — id сверху вниз (первая выше
        всех). Двигаем только роли ниже бота, перераспределяя между ними их же
        позиции — заблокированных зон (@everyone, managed, выше бота) не касаемся."""
        order = command.payload.get("order") or []
        roles = [self._manageable_role(guild, int(rid)) for rid in order]
        editable_now = {
            r for r in guild.roles if not r.is_default() and not r.managed and r < guild.me.top_role
        }
        # список должен совпасть с текущим набором редактируемых ролей один-в-один,
        # иначе панель устарела и перестановка сломала бы иерархию
        if set(roles) != editable_now:
            raise CommandError("Список ролей устарел — обнови страницу.")
        if len(roles) < 2:
            return "Нечего переставлять."
        slots = sorted(r.position for r in roles)  # позиции, что эти роли и занимают
        # верх списка (roles[0]) — самый высокий слот; идём с конца к началу
        positions = dict(zip(reversed(roles), slots, strict=True))
        try:
            await guild.edit_role_positions(positions=positions, reason="Панель: порядок ролей")
        except discord.Forbidden as exc:
            raise CommandError("Нет права Manage Roles.") from exc
        return "Порядок ролей обновлён."

    async def _role_permissions(self, guild: discord.Guild, command: Command) -> str:
        """Права роли с ограждениями. Здесь — НАСТОЯЩАЯ граница (панель отдельный
        процесс): Administrator панель не трогает никогда (бит берём из текущего
        значения), а права, которых нет у самого бота, оставляем как есть —
        Discord всё равно не даст их выдать, а так ещё и без лишнего 403."""
        p = command.payload
        role = self._manageable_role(guild, int(p["role_id"]))
        requested = discord.Permissions(int(p["permissions"]))
        current = role.permissions
        bot_perms = guild.me.guild_permissions
        result = discord.Permissions()
        for name, bot_has in bot_perms:
            if name == "administrator":
                setattr(result, name, current.administrator)  # панель admin не меняет
            elif bot_has:
                setattr(result, name, getattr(requested, name))
            else:
                setattr(result, name, getattr(current, name))  # боту недоступно — как есть
        if result.value == current.value:
            return "Права не изменились."
        try:
            await role.edit(permissions=result, reason="Панель: права роли")
        except discord.Forbidden as exc:
            raise CommandError("Нет права Manage Roles (или роль выше моей).") from exc
        return f"Обновил права роли «{role.name}»."

    # массовая выдача/снятие: серверы маленькие, поэтому синхронно одной командой
    # с лёгкой паузой (discord.py и сам ждёт на 429; пауза — доп. вежливость).
    # Кап — предохранитель: панель отдельный процесс, доверять размеру нельзя.
    _BULK_CAP = 200

    async def _role_bulk(self, guild: discord.Guild, command: Command) -> str:
        p = command.payload
        role = self._manageable_role(guild, int(p["role_id"]))
        op = str(p.get("op"))
        if op not in ("assign", "unassign"):
            raise CommandError("Неизвестная операция.")
        if op == "assign":
            # ботам роль пачкой не раздаём — только живым участникам без неё
            targets = [m for m in guild.members if not m.bot and role not in m.roles]
            reason = "Панель: массовая выдача роли"
        else:
            targets = [m for m in guild.members if role in m.roles]
            reason = "Панель: массовое снятие роли"
        if not targets:
            return "Некого менять — список пуст."
        if len(targets) > self._BULK_CAP:
            raise CommandError(
                f"Слишком много участников ({len(targets)} > {self._BULK_CAP}) — предел безопасности."
            )
        done = 0
        failed = 0
        for member in targets:
            try:
                if op == "assign":
                    await member.add_roles(role, reason=reason)
                else:
                    await member.remove_roles(role, reason=reason)
                done += 1
            except discord.Forbidden as exc:
                # прав нет вовсе — дальше смысла нет
                raise CommandError("Нет права Manage Roles (или роль выше моей).") from exc
            except discord.HTTPException:
                failed += 1
            await asyncio.sleep(0.2)
        verb = "Выдал" if op == "assign" else "Снял"
        msg = f"{verb} роль «{role.name}»: {done} чел."
        return msg + (f", ошибок {failed}." if failed else ".")

    # импорт набора ролей (шаблон/экспорт с другого сервера). Права роли НЕ
    # переносим (как и role.create — безопасно, Discord бы срезал недоступные
    # боту биты); создаём только недостающие по имени, чтобы повтор был безвреден.
    _IMPORT_CAP = 50

    async def _role_import(self, guild: discord.Guild, command: Command) -> str:
        specs = command.payload.get("roles")
        if not isinstance(specs, list):
            raise CommandError("Некорректный формат импорта.")
        if len(specs) > self._IMPORT_CAP:
            raise CommandError(
                f"Слишком много ролей ({len(specs)} > {self._IMPORT_CAP}) — предел безопасности."
            )
        existing = {r.name.casefold() for r in guild.roles}
        created = 0
        skipped = 0
        for spec in specs:
            if not isinstance(spec, dict):
                skipped += 1
                continue
            try:
                name = self._role_name(spec.get("name"))
            except CommandError:
                skipped += 1  # битое имя в файле — пропускаем, не валим весь импорт
                continue
            if name.casefold() in existing:
                skipped += 1  # роль с таким именем уже есть — не плодим дубли
                continue
            try:
                await guild.create_role(
                    name=name,
                    colour=self._role_colour(spec.get("color")),
                    hoist=bool(spec.get("hoist")),
                    mentionable=bool(spec.get("mentionable")),
                    reason="Панель: импорт ролей",
                )
            except discord.Forbidden as exc:
                raise CommandError("Нет права Manage Roles.") from exc
            existing.add(name.casefold())
            created += 1
            await asyncio.sleep(0.2)  # вежливость к rate limit
        if created == 0:
            return f"Ничего не создано (пропущено: {skipped})."
        msg = f"Создано ролей: {created}."
        return msg + (f" Пропущено (уже есть/битые): {skipped}." if skipped else "")


_HANDLERS = {
    "mod.tempban": DiscordCommandExecutor._tempban,
    "mod.unban": DiscordCommandExecutor._unban,
    "mod.mute": DiscordCommandExecutor._mute,
    "mod.unmute": DiscordCommandExecutor._unmute,
    "music.pause": DiscordCommandExecutor._pause,
    "music.resume": DiscordCommandExecutor._resume,
    "music.skip": DiscordCommandExecutor._skip,
    "music.stop": DiscordCommandExecutor._stop,
    "profile.apply": DiscordCommandExecutor._profile_apply,
    "role.assign": DiscordCommandExecutor._role_assign,
    "role.unassign": DiscordCommandExecutor._role_unassign,
    "role.create": DiscordCommandExecutor._role_create,
    "role.edit": DiscordCommandExecutor._role_edit,
    "role.delete": DiscordCommandExecutor._role_delete,
    "role.reorder": DiscordCommandExecutor._role_reorder,
    "role.permissions": DiscordCommandExecutor._role_permissions,
    "role.bulk": DiscordCommandExecutor._role_bulk,
    "role.import": DiscordCommandExecutor._role_import,
}
