"""Исполнитель команд моста на стороне бота: единственное место, где команда
панели превращается в реальное Discord-действие.

Модерация (бан/разбан/мут/анмут) и управление музыкой (пауза/продолжить/
пропустить/стоп). Всё исполнимо по id: бан/разбан через discord.Object, мут
через get/fetch_member, музыка — через живой MusicPlayerService гильдии.
Ожидаемые провалы (нет прав, участник/сессия не найдены) кидаются как
CommandError — панель покажет их админу.
"""

import base64
import binascii
import logging
from datetime import UTC, datetime, timedelta

import aiohttp
import discord
from discord.ext import commands as discord_commands

from src.application.moderation.di import ModerationContainer
from src.infrastructure.commands.bridge import Command, CommandError

logger = logging.getLogger(__name__)

# аватар/баннер Discord принимает картинкой; качаем по URL из панели. Лимит и
# проверка типа — чтобы не тянуть гигантский/непонятный файл.
_IMAGE_MAX_BYTES = 8 * 1024 * 1024


async def _download_image(url: str) -> bytes:
    if not url.startswith(("http://", "https://")):
        raise CommandError("URL картинки должен начинаться с http(s)://")
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
            raise CommandError(
                "Нет права Timeout Members (или роль участника выше моей)."
            ) from exc
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
            kwargs["nick"] = (str(p["nick"]).strip() or None)
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
}
