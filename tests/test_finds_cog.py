"""FindsCog: хелперы, отслеживание активности, кнопка «Сходить туда» во всех
исходах, команды /finds /collection /gift (+autocomplete) /walk."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.ai_chat.mood import MoodTracker
from src.application.finds.use_cases import (
    ActiveFindView,
    ClaimResult,
    CollectionEntry,
    GiftResult,
    WalkResult,
)
from src.domain.finds import catalog
from src.domain.finds.entities import NightFind, Rarity
from src.infrastructure.discord.cogs.finds import FindsCog, _ts
from tests.cog_fakes import make_interaction

NOW = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
COMMON = catalog.get_item("postcard_90s")  # COMMON
LEGENDARY = catalog.get_item("unsent_letter")  # LEGENDARY


def make_settings(**over):
    base = dict(
        finds_channel="",
        finds_channel_id=0,
        main_channel="основной",
        holidays={"01-01": "НГ"},
        finds_min_interval_hours=12,
        finds_max_interval_hours=48,
    )
    base.update(over)
    return SimpleNamespace(**base)


def make_container():
    c = SimpleNamespace()
    c.claim_find = SimpleNamespace(execute=AsyncMock())
    c.get_active_find = SimpleNamespace(execute=AsyncMock(return_value=None))
    c.get_collection = SimpleNamespace(execute=AsyncMock(return_value=[]))
    c.gift_item = SimpleNamespace(execute=AsyncMock())
    c.special_walk = SimpleNamespace(execute=AsyncMock())
    c.list_live_finds = SimpleNamespace(execute=AsyncMock(return_value=[]))
    c.register_find_message = SimpleNamespace(execute=AsyncMock())
    return c


def make_cog(container=None, settings=None):
    bot = MagicMock()
    return FindsCog(bot, container or make_container(), settings or make_settings(), MoodTracker())


def claim_interaction():
    interaction = make_interaction()
    interaction.message = MagicMock()
    interaction.message.id = 200
    interaction.message.embeds = []
    interaction.message.edit = AsyncMock()
    interaction.channel.send = AsyncMock()
    return interaction


# --- хелперы ----------------------------------------------------------------


def test_ts_format():
    assert _ts(NOW) == f"<t:{int(NOW.timestamp())}:R>"


def test_holiday_key():
    cog = make_cog(settings=make_settings(holidays={"11-07": "X"}))
    assert cog._holiday_key(NOW) == "11-07"
    assert cog._holiday_key(NOW.replace(month=3, day=3)) is None


def test_announce_channel_by_name():
    cog = make_cog(settings=make_settings(main_channel="основной"))
    guild = MagicMock()
    channel = SimpleNamespace(name="основной")
    guild.text_channels = [SimpleNamespace(name="прочее"), channel]
    assert cog._announce_channel(guild) is channel


def test_announce_channel_prefers_config_id():
    import discord
    from unittest.mock import MagicMock as MM

    gs = MM()
    gs.get = MM(return_value=555)  # /config finds_channel_id
    cog = make_cog()
    cog.gs = gs
    guild = MagicMock()
    forum_ch = MM(spec=discord.TextChannel)
    guild.get_channel = MM(return_value=forum_ch)
    assert cog._announce_channel(guild) is forum_ch


async def test_spawnfind_no_channel():
    cog = make_cog()
    interaction = make_interaction()
    interaction.guild.text_channels = []  # канала «основной» нет
    interaction.guild.get_channel = MagicMock(return_value=None)
    await type(cog).spawn_find_command.callback(cog, interaction)
    assert "Не нашла канал" in interaction.response.send_message.await_args.args[0]


async def test_spawnfind_active_exists():
    container = make_container()
    from src.application.finds.use_cases import ActiveFindView

    find = NightFind(
        guild_id=10,
        location_id="nezu_square",
        item_id="postcard_90s",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    container.get_active_find.execute.return_value = ActiveFindView(
        find=find, location=catalog.get_location("nezu_square"), item=COMMON
    )
    cog = make_cog(container, settings=make_settings(main_channel="c"))
    interaction = make_interaction()
    ch = SimpleNamespace(name="c")
    interaction.guild.text_channels = [ch]
    await type(cog).spawn_find_command.callback(cog, interaction)
    assert "уже висит" in interaction.response.send_message.await_args.args[0]


async def test_spawnfind_forces_spawn(monkeypatch):
    from unittest.mock import AsyncMock as AM

    cog = make_cog(settings=make_settings(main_channel="c"))
    interaction = make_interaction()
    channel = SimpleNamespace(name="c", mention="#c")
    interaction.guild.text_channels = [channel]
    cog.finds.get_active_find.execute = AM(return_value=None)
    find = NightFind(
        guild_id=10,
        location_id="x",
        item_id="y",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    cog._try_spawn = AM(return_value=find)
    await type(cog).spawn_find_command.callback(cog, interaction)
    cog._try_spawn.assert_awaited_once()
    assert cog._try_spawn.await_args.kwargs.get("force") is True or cog._try_spawn.await_args.args[
        1:
    ] == (True,)
    assert "заспавнена" in interaction.followup.send.await_args.args[0]


async def test_on_message_tracks_main_activity():
    cog = make_cog(settings=make_settings(main_channel="основной"))
    msg = MagicMock()
    msg.author = SimpleNamespace(bot=False)
    msg.guild = SimpleNamespace(id=10)
    msg.channel = SimpleNamespace(name="основной")
    await cog.on_message(msg)
    assert 10 in cog._main_last_activity


# --- handle_claim: все исходы ----------------------------------------------


async def test_claim_gone():
    container = make_container()
    container.claim_find.execute.return_value = ClaimResult(status="gone")
    cog = make_cog(container)
    interaction = claim_interaction()
    await cog.handle_claim(interaction)
    assert "уже пусто" in interaction.followup.send.await_args.args[0]


async def test_claim_already():
    container = make_container()
    container.claim_find.execute.return_value = ClaimResult(status="already")
    cog = make_cog(container)
    interaction = claim_interaction()
    await cog.handle_claim(interaction)
    assert "уже ходил" in interaction.followup.send.await_args.args[0]


async def test_claim_cooldown():
    container = make_container()
    container.claim_find.execute.return_value = ClaimResult(
        status="cooldown", retry_at=NOW + timedelta(hours=2)
    )
    cog = make_cog(container)
    interaction = claim_interaction()
    await cog.handle_claim(interaction)
    assert "гудят" in interaction.followup.send.await_args.args[0]


async def test_claim_fail():
    container = make_container()
    container.claim_find.execute.return_value = ClaimResult(
        status="fail", points_delta=-5, points_total=20
    )
    cog = make_cog(container)
    interaction = claim_interaction()
    await cog.handle_claim(interaction)
    assert "-5 очков" in interaction.followup.send.await_args.args[0]


async def test_claim_success_announces():
    container = make_container()
    container.claim_find.execute.return_value = ClaimResult(
        status="success", item=LEGENDARY, points_delta=150, points_total=500, level=6
    )
    cog = make_cog(container)
    interaction = claim_interaction()
    await cog.handle_claim(interaction)
    interaction.channel.send.assert_awaited_once()  # публичный анонс
    # эфемерное подтверждение с названием предмета
    assert LEGENDARY.name in interaction.followup.send.await_args.args[0]


# --- /finds -----------------------------------------------------------------


async def test_finds_command_none():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).finds_command.callback(cog, interaction)
    assert "находок нет" in interaction.response.send_message.await_args.args[0]


async def test_finds_command_active():
    container = make_container()
    find = NightFind(
        guild_id=10,
        location_id="nezu_square",
        item_id="postcard_90s",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=2),
        channel_id=100,
        message_id=200,
    )
    view = ActiveFindView(find=find, location=catalog.get_location("nezu_square"), item=COMMON)
    container.get_active_find.execute.return_value = view
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).finds_command.callback(cog, interaction)
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert "Активная находка" in embed.title


# --- /collection ------------------------------------------------------------


async def test_collection_empty():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).collection_command.callback(cog, interaction)
    assert "пусто" in interaction.followup.send.await_args.args[0]


async def test_collection_grouped_by_rarity():
    container = make_container()
    container.get_collection.execute.return_value = [
        CollectionEntry(item=LEGENDARY, obtained_at=NOW, gifted_at=None),
        CollectionEntry(item=COMMON, obtained_at=NOW, gifted_at=NOW),
    ]
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).collection_command.callback(cog, interaction)
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert LEGENDARY.name in embed.description
    assert "подарено" in embed.description  # отметка у gifted


# --- /gift + autocomplete ---------------------------------------------------


async def test_gift_not_owned():
    container = make_container()
    container.gift_item.execute.return_value = GiftResult(status="no_item")
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).gift_command.callback(cog, interaction, "unknown")
    assert "нет такого" in interaction.followup.send.await_args.args[0]


async def test_gift_success():
    container = make_container()
    container.gift_item.execute.return_value = GiftResult(
        status="ok", item=COMMON, bonus=20, points_total=100
    )
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).gift_command.callback(cog, interaction, "postcard_90s")
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert COMMON.name in embed.title


async def test_gift_autocomplete_filters():
    container = make_container()
    container.get_collection.execute.return_value = [
        CollectionEntry(item=COMMON, obtained_at=NOW, gifted_at=None),
        CollectionEntry(item=LEGENDARY, obtained_at=NOW, gifted_at=NOW),  # подарен — скрыт
    ]
    cog = make_cog(container)
    interaction = make_interaction()
    choices = await type(cog).gift_autocomplete(cog, interaction, "")
    values = [c.value for c in choices]
    assert "postcard_90s" in values
    assert "unsent_letter" not in values  # уже подарен


# --- /walk ------------------------------------------------------------------


async def test_walk_cooldown():
    container = make_container()
    container.special_walk.execute.return_value = WalkResult(
        status="cooldown", retry_at=NOW + timedelta(days=3)
    )
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).walk_command.callback(cog, interaction)
    assert "не такси" in interaction.followup.send.await_args.args[0]


async def test_walk_poor():
    container = make_container()
    container.special_walk.execute.return_value = WalkResult(
        status="poor", cost=60, points_total=10
    )
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).walk_command.callback(cog, interaction)
    assert "заслужи" in interaction.followup.send.await_args.args[0]


async def test_walk_success():
    container = make_container()
    container.special_walk.execute.return_value = WalkResult(
        status="success", item=COMMON, points_delta=-40, points_total=100, cost=60
    )
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).walk_command.callback(cog, interaction)
    assert COMMON.name in interaction.followup.send.await_args.args[0]
