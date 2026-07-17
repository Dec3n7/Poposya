"""TempVoiceCog: создание каморки по входу в хаб, удаление по опустошению,
подметание осиротевших. Discord — фейки, БД — фейковый контейнер."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from src.domain.tempvoice.entities import TempChannel
from src.infrastructure.discord.cogs.tempvoice.cog import TempVoiceCog

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)

HUB_ID = 500
CATEGORY_ID = 400
OWNER_ID = 1


def make_cog(hub=HUB_ID, category=0, max_per_guild=25, default_limit=0):
    container = MagicMock()
    container.get.execute = AsyncMock(return_value=None)
    container.count.execute = AsyncMock(return_value=0)
    container.register.execute = AsyncMock()
    container.release.execute = AsyncMock(return_value=True)
    container.list_channels.execute = AsyncMock(return_value=[])
    settings = SimpleNamespace(
        tempvoice_hub_channel=hub,
        tempvoice_category=category,
        tempvoice_max_per_guild=max_per_guild,
        tempvoice_default_limit=default_limit,
    )
    cog = TempVoiceCog(MagicMock(), container, settings)
    return cog, container


def _voice_channel(channel_id, members=(), category=None):
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.id = channel_id
    channel.members = list(members)
    channel.category = category
    channel.delete = AsyncMock()
    return channel


def _member(uid=OWNER_ID, is_bot=False, name="Гость", guild=None):
    member = MagicMock()
    member.id = uid
    member.bot = is_bot
    member.display_name = name
    member.send = AsyncMock()
    member.move_to = AsyncMock()
    member.guild = guild if guild is not None else _guild()
    member.__str__ = lambda self: name
    return member


def _guild(guild_id=10, created=None):
    guild = MagicMock()
    guild.id = guild_id
    guild.create_voice_channel = AsyncMock(return_value=created or _voice_channel(600))
    guild.get_channel = MagicMock(return_value=None)
    return guild


def _state(channel):
    return SimpleNamespace(channel=channel)


# --- создание ---


async def test_join_hub_creates_channel_and_moves_owner():
    cog, container = make_cog()
    created = _voice_channel(600)
    guild = _guild(created=created)
    member = _member(guild=guild)
    hub = _voice_channel(HUB_ID)

    await cog.on_voice_state_update(member, _state(None), _state(hub))

    guild.create_voice_channel.assert_awaited_once()
    assert "Каморка Гость" in guild.create_voice_channel.await_args.kwargs["name"]
    member.move_to.assert_awaited_once_with(created, reason="Каморка: перенос владельца")
    container.register.execute.assert_awaited_once()
    args = container.register.execute.await_args.args
    assert args[:3] == (10, 600, OWNER_ID)  # guild, channel, owner


async def test_join_other_channel_does_nothing():
    cog, container = make_cog()
    member = _member()
    await cog.on_voice_state_update(member, _state(None), _state(_voice_channel(999)))
    member.guild.create_voice_channel.assert_not_awaited()


async def test_feature_off_when_hub_not_configured():
    cog, container = make_cog(hub=0)
    member = _member()
    await cog.on_voice_state_update(member, _state(None), _state(_voice_channel(0)))
    member.guild.create_voice_channel.assert_not_awaited()


async def test_bots_are_ignored():
    cog, _ = make_cog()
    member = _member(is_bot=True)
    await cog.on_voice_state_update(member, _state(None), _state(_voice_channel(HUB_ID)))
    member.guild.create_voice_channel.assert_not_awaited()


async def test_mute_does_not_recreate_channel():
    # before.channel == after.channel: человек лишь заглушился
    cog, _ = make_cog()
    member = _member()
    hub = _voice_channel(HUB_ID)
    await cog.on_voice_state_update(member, _state(hub), _state(hub))
    member.guild.create_voice_channel.assert_not_awaited()


async def test_cap_reached_refuses_with_dm():
    cog, container = make_cog(max_per_guild=2)
    container.count.execute = AsyncMock(return_value=2)
    member = _member()
    await cog.on_voice_state_update(member, _state(None), _state(_voice_channel(HUB_ID)))
    member.guild.create_voice_channel.assert_not_awaited()
    member.send.assert_awaited_once()  # объяснили, почему ничего не вышло


async def test_default_limit_applied():
    cog, _ = make_cog(default_limit=4)
    member = _member()
    await cog.on_voice_state_update(member, _state(None), _state(_voice_channel(HUB_ID)))
    assert member.guild.create_voice_channel.await_args.kwargs["user_limit"] == 4


async def test_configured_category_used():
    cog, _ = make_cog(category=CATEGORY_ID)
    category = MagicMock(spec=discord.CategoryChannel)
    member = _member()
    member.guild.get_channel = MagicMock(return_value=category)
    await cog.on_voice_state_update(member, _state(None), _state(_voice_channel(HUB_ID)))
    assert member.guild.create_voice_channel.await_args.kwargs["category"] is category


async def test_non_category_setting_falls_back_to_hub_category():
    # в /config выбрали текстовый канал вместо категории — не падаем
    cog, _ = make_cog(category=CATEGORY_ID)
    member = _member()
    member.guild.get_channel = MagicMock(return_value=MagicMock(spec=discord.TextChannel))
    hub_category = MagicMock(spec=discord.CategoryChannel)
    hub = _voice_channel(HUB_ID, category=hub_category)
    await cog.on_voice_state_update(member, _state(None), _state(hub))
    assert member.guild.create_voice_channel.await_args.kwargs["category"] is hub_category


async def test_cooldown_blocks_second_channel():
    cog, container = make_cog()
    member = _member()
    hub = _voice_channel(HUB_ID)
    await cog.on_voice_state_update(member, _state(None), _state(hub))
    await cog.on_voice_state_update(member, _state(None), _state(hub))
    assert member.guild.create_voice_channel.await_count == 1  # второй раз — кулдаун


# --- отказы Discord ---


async def test_missing_manage_channels_is_explained():
    cog, container = make_cog()
    member = _member()
    member.guild.create_voice_channel = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "no perms")
    )
    await cog.on_voice_state_update(member, _state(None), _state(_voice_channel(HUB_ID)))
    member.send.assert_awaited_once()
    container.register.execute.assert_not_awaited()  # нечего регистрировать


async def test_failed_move_deletes_channel_and_row():
    """Нет права Move Members: пустую каморку нельзя оставлять — событий
    выхода по ней не придёт, удалить её будет уже некому."""
    cog, container = make_cog()
    created = _voice_channel(600)
    member = _member(guild=_guild(created=created))
    member.move_to = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no move"))

    await cog.on_voice_state_update(member, _state(None), _state(_voice_channel(HUB_ID)))

    created.delete.assert_awaited_once()
    container.release.execute.assert_awaited_once_with(600)
    member.send.assert_awaited_once()


async def test_no_cooldown_after_failed_creation():
    # неудача не должна запирать человека на 30 секунд
    cog, container = make_cog()
    member = _member()
    member.guild.create_voice_channel = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "no perms")
    )
    hub = _voice_channel(HUB_ID)
    await cog.on_voice_state_update(member, _state(None), _state(hub))
    member.guild.create_voice_channel = AsyncMock(return_value=_voice_channel(600))
    await cog.on_voice_state_update(member, _state(None), _state(hub))
    member.guild.create_voice_channel.assert_awaited_once()  # вторая попытка прошла


# --- удаление ---


async def test_last_human_leaves_deletes_channel():
    cog, container = make_cog()
    temp = _voice_channel(600, members=[])
    container.get.execute = AsyncMock(return_value=TempChannel(10, 600, OWNER_ID, NOW))
    member = _member()
    await cog.on_voice_state_update(member, _state(temp), _state(None))
    temp.delete.assert_awaited_once()
    container.release.execute.assert_awaited_once_with(600)


async def test_channel_survives_while_humans_remain():
    cog, container = make_cog()
    temp = _voice_channel(600, members=[_member(uid=2)])
    container.get.execute = AsyncMock(return_value=TempChannel(10, 600, OWNER_ID, NOW))
    await cog.on_voice_state_update(_member(), _state(temp), _state(None))
    temp.delete.assert_not_awaited()


async def test_music_bot_alone_does_not_keep_channel_alive():
    """Попося с музыкой остаётся в канале — но она не человек: иначе каморка
    не опустеет никогда."""
    cog, container = make_cog()
    temp = _voice_channel(600, members=[_member(uid=99, is_bot=True)])
    container.get.execute = AsyncMock(return_value=TempChannel(10, 600, OWNER_ID, NOW))
    await cog.on_voice_state_update(_member(), _state(temp), _state(None))
    temp.delete.assert_awaited_once()


async def test_leaving_foreign_channel_ignored():
    cog, container = make_cog()
    foreign = _voice_channel(700, members=[])
    container.get.execute = AsyncMock(return_value=None)  # не наша
    await cog.on_voice_state_update(_member(), _state(foreign), _state(None))
    foreign.delete.assert_not_awaited()


async def test_move_from_own_channel_to_hub_frees_and_creates():
    """Ушёл из своей каморки прямо в хаб: старая освобождается, новая создаётся."""
    cog, container = make_cog()
    old = _voice_channel(600, members=[])
    created = _voice_channel(601)
    member = _member(guild=_guild(created=created))
    container.get.execute = AsyncMock(return_value=TempChannel(10, 600, OWNER_ID, NOW))

    await cog.on_voice_state_update(member, _state(old), _state(_voice_channel(HUB_ID)))

    old.delete.assert_awaited_once()
    member.guild.create_voice_channel.assert_awaited_once()


# --- подметание после рестарта ---


async def test_sweep_deletes_empty_and_forgets_missing():
    cog, container = make_cog()
    empty = _voice_channel(600, members=[])
    alive = _voice_channel(601, members=[_member(uid=2)])
    container.list_channels.execute = AsyncMock(
        return_value=[
            TempChannel(10, 600, OWNER_ID, NOW),  # пустая — снести
            TempChannel(10, 601, OWNER_ID, NOW),  # живая — оставить
            TempChannel(10, 602, OWNER_ID, NOW),  # канала уже нет — забыть строку
        ]
    )
    guild = _guild()
    guild.get_channel = MagicMock(side_effect={600: empty, 601: alive, 602: None}.get)
    cog.bot.guilds = [guild]

    await cog.on_ready()

    empty.delete.assert_awaited_once()
    alive.delete.assert_not_awaited()
    released = {c.args[0] for c in container.release.execute.await_args_list}
    assert released == {600, 602}


async def test_sweep_runs_only_once():
    # on_ready повторяется при реконнектах — второй проход не нужен
    cog, container = make_cog()
    cog.bot.guilds = [_guild()]
    await cog.on_ready()
    await cog.on_ready()
    assert container.list_channels.execute.await_count == 1
