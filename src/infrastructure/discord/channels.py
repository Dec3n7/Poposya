"""Резолв каналов сервера: заданный per-guild ID → легаси-имя из .env → фолбэк.

До этого главный/приветственный/альбомный каналы искались ТОЛЬКО по имени
(«основной», «bots», «альбом-попоси») — на чужом сервере таких нет, и фичи тихо
немели. Теперь: сначала канал, заданный в /config по ID, затем легаси-имя (для
домашнего сервера), затем — по желанию — фолбэк на system_channel (первый
доступный), чтобы бот работал «из коробки». Общая точка: одни и те же правила
нужны и для отправки, и для проверки «это главный канал?» в разных когах."""

import discord


def first_postable(guild: discord.Guild) -> discord.TextChannel | None:
    """system_channel, если туда можно писать; иначе первый доступный текстовый."""
    me = guild.me
    if me is None:
        return None
    system = guild.system_channel
    if system is not None and system.permissions_for(me).send_messages:
        return system
    for channel in guild.text_channels:
        perms = channel.permissions_for(me)
        if perms.view_channel and perms.send_messages:
            return channel
    return None


def resolve_channel(
    guild: discord.Guild,
    channel_id: int,
    name: str,
    *,
    fallback: bool,
) -> discord.TextChannel | None:
    """Канал по ID (per-guild) → по легаси-имени → фолбэк на first_postable / None."""
    if channel_id:
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
    if name:
        by_name = discord.utils.get(guild.text_channels, name=name)
        if by_name is not None:
            return by_name
    return first_postable(guild) if fallback else None


def is_designated_main(channel: object, channel_id: int, name: str) -> bool:
    """channel — НАЗНАЧЕННЫЙ главный: по заданному ID, иначе по легаси-имени.

    БЕЗ фолбэка намеренно: реактивные триггеры (буст настроения, пассивные
    реплики) не должны срабатывать в авто-подобранном канале, который админ
    главным не назначал. Мок-безопасно (getattr, без isinstance)."""
    if channel is None:
        return False
    if channel_id:
        return getattr(channel, "id", None) == channel_id
    return bool(name) and getattr(channel, "name", None) == name
