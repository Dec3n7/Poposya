"""FindsCog: хелперы, отслеживание активности, кнопка «Сходить туда» во всех
исходах, команды /finds /collection /gift (+autocomplete) /walk, фоновые циклы
спавна/протухания находок, cog_load/unload."""

import asyncio
import contextlib
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.application.ai_chat.mood import MoodTracker
from src.application.finds.use_cases import (
    ActiveFindView,
    ClaimResult,
    CollectionEntry,
    GiftResult,
    WalkResult,
)
from src.application.persona.registry import PHRASE_SPECS
from src.domain.finds import catalog
from src.domain.finds.entities import NightFind
from src.infrastructure.discord.cogs import finds as finds_module
from src.infrastructure.discord.cogs.finds import (
    FindClaimView,
    FindsCog,
    _ts,
)
from tests.cog_fakes import http_error, make_interaction

# реакции успеха теперь в каталоге фраз (P4); дефолты — 1:1 со старыми константами
_SUCCESS_LOW = PHRASE_SPECS["finds.success_low"].default
_SUCCESS_MID = PHRASE_SPECS["finds.success_mid"].default
_SUCCESS_HIGH = PHRASE_SPECS["finds.success_high"].default
_SUCCESS_LEGENDARY = PHRASE_SPECS["finds.success_legendary"].default

NOW = datetime(2026, 7, 11, 22, 0, tzinfo=UTC)
COMMON = catalog.get_item("postcard_90s")  # COMMON
LEGENDARY = catalog.get_item("unsent_letter")  # LEGENDARY


def make_settings(**over):
    base = dict(
        finds_channel="",
        finds_channel_id=0,
        main_channel="основной",
        main_channel_id=0,
        holidays={"01-01": "НГ"},
        finds_min_interval_hours=12,
        finds_max_interval_hours=48,
        finds_enabled=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def make_container():
    c = SimpleNamespace()
    c.claim_find = SimpleNamespace(execute=AsyncMock())
    c.get_active_find = SimpleNamespace(execute=AsyncMock(return_value=None))
    c.get_collection = SimpleNamespace(execute=AsyncMock(return_value=[]))
    c.gift_item = SimpleNamespace(execute=AsyncMock())
    c.spawn_find = SimpleNamespace(execute=AsyncMock())
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
    from unittest.mock import MagicMock as MM

    import discord

    gs = MM()
    gs.get = MM(return_value=555)  # /config finds_channel_id
    cog = make_cog()
    cog.gs = gs
    guild = MagicMock()
    forum_ch = MM(spec=discord.TextChannel)
    guild.get_channel = MM(return_value=forum_ch)
    assert cog._announce_channel(guild) is forum_ch


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


async def test_walk_fail():
    container = make_container()
    container.special_walk.execute.return_value = WalkResult(
        status="fail", cost=60, points_total=40
    )
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).walk_command.callback(cog, interaction)
    assert "60 очков" in interaction.followup.send.await_args.args[0]


# --- interaction_check / _roll_interval -------------------------------------


async def test_interaction_check_allows_when_module_enabled():
    cog = make_cog()
    interaction = make_interaction()
    assert await cog.interaction_check(interaction) is True


async def test_interaction_check_blocks_when_module_disabled():
    cog = make_cog(settings=make_settings(finds_enabled=False))
    interaction = make_interaction()
    result = await cog.interaction_check(interaction)
    assert result is False
    interaction.response.send_message.assert_awaited_once()


def test_roll_interval_within_configured_bounds():
    cog = make_cog(settings=make_settings(finds_min_interval_hours=1, finds_max_interval_hours=1))
    assert cog._roll_interval(10) == 1 * 3600


def test_roll_interval_clamps_max_to_min():
    cog = make_cog(settings=make_settings(finds_min_interval_hours=5, finds_max_interval_hours=1))
    # hi(1) < lo(5) — _roll_interval поднимает hi до lo, uniform(5, 5) детерминирован
    seconds = cog._roll_interval(10)
    assert seconds == 5 * 3600


# --- cog_load / cog_unload ---------------------------------------------------


async def test_cog_load_registers_persistent_claim_view():
    cog = make_cog()
    await cog.cog_load()
    cog.bot.add_view.assert_called_once()
    assert isinstance(cog.bot.add_view.call_args.args[0], FindClaimView)


def test_cog_unload_cancels_tasks_and_expiry_tasks():
    cog = make_cog()
    loop_task = MagicMock()
    expiry_task = MagicMock()
    cog._tasks = [loop_task]
    cog._expiry_tasks = {1: expiry_task}
    cog.cog_unload()
    loop_task.cancel.assert_called_once()
    expiry_task.cancel.assert_called_once()


# --- on_message: игнор ботов/ЛС ---------------------------------------------


async def test_on_message_ignores_bots():
    cog = make_cog()
    msg = MagicMock()
    msg.author = SimpleNamespace(bot=True)
    await cog.on_message(msg)
    assert cog._main_last_activity == {}


async def test_on_message_ignores_dm():
    cog = make_cog()
    msg = MagicMock()
    msg.author = SimpleNamespace(bot=False)
    msg.guild = None
    await cog.on_message(msg)
    assert cog._main_last_activity == {}


# --- on_ready -----------------------------------------------------------------


async def test_on_ready_starts_loops_once():
    cog = make_cog()
    guild = SimpleNamespace(id=10)
    cog.bot.guilds = [guild]
    cog._spawn_loop = AsyncMock()
    cog._restore_live_finds = AsyncMock()
    await cog.on_ready()
    assert cog._loops_started is True
    assert len(cog._tasks) == 2
    await asyncio.sleep(0)  # дать AsyncMock-корутинам завершиться
    await cog.on_ready()  # второй вызов не должен пересоздавать задачи
    assert len(cog._tasks) == 2


# --- _restore_live_finds -----------------------------------------------------


async def test_restore_live_finds_schedules_expiry_for_each():
    container = make_container()
    find = NightFind(
        guild_id=10,
        location_id="nezu_square",
        item_id="postcard_90s",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        id=1,
    )
    container.list_live_finds.execute.return_value = [find]
    cog = make_cog(container)
    cog._schedule_expiry = MagicMock()
    await cog._restore_live_finds()
    cog._schedule_expiry.assert_called_once_with(find)


async def test_restore_live_finds_swallows_exception():
    container = make_container()
    container.list_live_finds.execute.side_effect = RuntimeError("boom")
    cog = make_cog(container)
    await cog._restore_live_finds()  # не падает


# --- _spawn_loop ---------------------------------------------------------------


async def _drive_spawn_loop(monkeypatch, cog, iterations):
    calls = 0

    async def fake_sleep(_):
        nonlocal calls
        calls += 1
        if calls > iterations:
            raise asyncio.CancelledError()

    monkeypatch.setattr(finds_module.asyncio, "sleep", fake_sleep)
    with contextlib.suppress(asyncio.CancelledError):
        await cog._spawn_loop()


async def test_spawn_loop_skips_disabled_guild(monkeypatch):
    gs = MagicMock()
    gs.get.return_value = False  # finds_enabled=False на сервере
    cog = make_cog()
    cog.gs = gs
    guild = SimpleNamespace(id=10)
    cog.bot.guilds = [guild]
    cog._try_spawn = AsyncMock()
    await _drive_spawn_loop(monkeypatch, cog, iterations=1)
    cog._try_spawn.assert_not_awaited()


async def test_spawn_loop_first_tick_sets_next_spawn_without_spawning(monkeypatch):
    cog = make_cog(settings=make_settings(finds_min_interval_hours=1, finds_max_interval_hours=1))
    guild = SimpleNamespace(id=10)
    cog.bot.guilds = [guild]
    cog._try_spawn = AsyncMock()
    await _drive_spawn_loop(monkeypatch, cog, iterations=1)
    assert 10 in cog._next_spawn
    cog._try_spawn.assert_not_awaited()


async def test_spawn_loop_skips_when_not_due_yet(monkeypatch):
    cog = make_cog()
    guild = SimpleNamespace(id=10)
    cog.bot.guilds = [guild]
    cog._next_spawn[10] = time.monotonic() + 999
    cog._try_spawn = AsyncMock()
    await _drive_spawn_loop(monkeypatch, cog, iterations=1)
    cog._try_spawn.assert_not_awaited()


async def test_spawn_loop_spawns_when_due_and_reschedules(monkeypatch):
    cog = make_cog(settings=make_settings(finds_min_interval_hours=1, finds_max_interval_hours=1))
    guild = SimpleNamespace(id=10)
    cog.bot.guilds = [guild]
    cog._next_spawn[10] = time.monotonic() - 1
    cog._try_spawn = AsyncMock()
    await _drive_spawn_loop(monkeypatch, cog, iterations=1)
    cog._try_spawn.assert_awaited_once_with(guild)
    assert cog._next_spawn[10] > time.monotonic()


async def test_spawn_loop_survives_try_spawn_exception(monkeypatch):
    cog = make_cog()
    guild = SimpleNamespace(id=10)
    cog.bot.guilds = [guild]
    cog._next_spawn[10] = time.monotonic() - 1
    cog._try_spawn = AsyncMock(side_effect=RuntimeError("boom"))
    await _drive_spawn_loop(monkeypatch, cog, iterations=1)  # не падает


# --- _try_spawn ----------------------------------------------------------------


async def test_try_spawn_no_channel_returns_none():
    cog = make_cog(settings=make_settings(main_channel="x"))
    guild = MagicMock()
    guild.id = 10
    guild.text_channels = []
    guild.get_channel = MagicMock(return_value=None)
    assert await cog._try_spawn(guild) is None


async def test_try_spawn_bad_mood_may_skip():
    cog = make_cog(settings=make_settings(main_channel="c"))
    guild = MagicMock()
    guild.id = 10
    guild.text_channels = [SimpleNamespace(name="c")]
    cog.mood.bump(10, -100)  # настроение <= 30
    cog._rng = SimpleNamespace(random=lambda: 0.1)  # < 0.5 -> пропускаем
    result = await cog._try_spawn(guild)
    assert result is None
    cog.finds.spawn_find.execute.assert_not_awaited()


async def test_try_spawn_active_find_already_present_returns_none():
    container = make_container()
    container.spawn_find = SimpleNamespace(execute=AsyncMock(return_value=None))
    cog = make_cog(container, settings=make_settings(main_channel="c"))
    guild = MagicMock()
    guild.id = 10
    guild.text_channels = [SimpleNamespace(name="c")]
    cog._main_last_activity[10] = time.monotonic()  # сервер «живой» — гейт активности пройден
    result = await cog._try_spawn(guild)
    assert result is None


async def test_try_spawn_full_success_flow():
    container = make_container()
    location = catalog.get_location("nezu_square")
    find = NightFind(
        guild_id=10,
        location_id=location.id,
        item_id="postcard_90s",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=2),
        id=77,
    )
    container.spawn_find = SimpleNamespace(execute=AsyncMock(return_value=(find, location, COMMON)))
    cog = make_cog(container, settings=make_settings(main_channel="c"))
    guild = MagicMock()
    guild.id = 10
    channel = MagicMock()
    channel.name = "c"
    channel.id = 100
    channel.send = AsyncMock(return_value=SimpleNamespace(id=555))
    guild.text_channels = [channel]
    cog._main_last_activity[10] = time.monotonic()  # сервер «живой» — гейт активности пройден
    try:
        result = await cog._try_spawn(guild)
        assert result is find
        channel.send.assert_awaited_once()
        container.register_find_message.execute.assert_awaited_once_with(77, 100, 555)
        assert (find.channel_id, find.message_id) == (100, 555)
        assert 77 in cog._expiry_tasks
    finally:
        for task in cog._expiry_tasks.values():
            task.cancel()


async def test_try_spawn_send_http_exception_returns_none():
    container = make_container()
    location = catalog.get_location("nezu_square")
    find = NightFind(
        guild_id=10,
        location_id=location.id,
        item_id="postcard_90s",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=2),
        id=78,
    )
    container.spawn_find = SimpleNamespace(execute=AsyncMock(return_value=(find, location, COMMON)))
    cog = make_cog(container, settings=make_settings(main_channel="c"))
    guild = MagicMock()
    guild.id = 10
    channel = MagicMock()
    channel.name = "c"
    channel.send = AsyncMock(side_effect=http_error())
    guild.text_channels = [channel]
    cog._main_last_activity[10] = time.monotonic()  # сервер «живой» — гейт активности пройден
    result = await cog._try_spawn(guild)
    assert result is None
    container.register_find_message.execute.assert_not_awaited()


# --- _schedule_expiry / _expire_later / _close_announcement -------------------


async def test_schedule_expiry_dedupes_by_find_id():
    cog = make_cog()
    find = NightFind(
        guild_id=10,
        location_id="x",
        item_id="y",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        id=1,
    )
    cog._schedule_expiry(find)
    task = cog._expiry_tasks[1]
    cog._schedule_expiry(find)  # уже запланировано — не дублируем
    assert cog._expiry_tasks[1] is task
    task.cancel()


def test_schedule_expiry_noop_without_id():
    cog = make_cog()
    find = NightFind(
        guild_id=10,
        location_id="x",
        item_id="y",
        created_at=NOW,
        expires_at=NOW,
        id=None,
    )
    cog._schedule_expiry(find)
    assert cog._expiry_tasks == {}


async def test_expire_later_waits_for_expiry(monkeypatch):
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(finds_module.asyncio, "sleep", fake_sleep)
    container = make_container()
    find = NightFind(
        guild_id=10,
        location_id="x",
        item_id="y",
        created_at=NOW,
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
        id=1,
    )
    cog = make_cog(container)
    cog._close_announcement = AsyncMock()
    await cog._expire_later(find)
    assert sleeps and sleeps[0] > 0
    cog._close_announcement.assert_awaited_once()


async def test_expire_later_still_active_skips_close():
    container = make_container()
    find = NightFind(
        guild_id=10,
        location_id="x",
        item_id="y",
        created_at=NOW,
        expires_at=NOW - timedelta(seconds=1),
        id=1,
    )
    container.get_active_find.execute.return_value = SimpleNamespace(find=SimpleNamespace(id=1))
    cog = make_cog(container)
    cog._close_announcement = AsyncMock()
    await cog._expire_later(find)
    cog._close_announcement.assert_not_awaited()


async def test_expire_later_fresh_list_contains_find_skips_close():
    container = make_container()
    find = NightFind(
        guild_id=10,
        location_id="x",
        item_id="y",
        created_at=NOW,
        expires_at=NOW - timedelta(seconds=1),
        id=1,
    )
    container.get_active_find.execute.return_value = None
    container.list_live_finds.execute.return_value = [find]
    cog = make_cog(container)
    cog._close_announcement = AsyncMock()
    await cog._expire_later(find)
    cog._close_announcement.assert_not_awaited()


async def test_expire_later_closes_when_truly_gone():
    container = make_container()
    find = NightFind(
        guild_id=10,
        location_id="x",
        item_id="y",
        created_at=NOW,
        expires_at=NOW - timedelta(seconds=1),
        id=1,
    )
    container.get_active_find.execute.return_value = None
    container.list_live_finds.execute.return_value = []
    cog = make_cog(container)
    cog._close_announcement = AsyncMock()
    await cog._expire_later(find)
    cog._close_announcement.assert_awaited_once()


async def test_close_announcement_noop_without_ids():
    cog = make_cog()
    find = NightFind(
        guild_id=10,
        location_id="x",
        item_id="y",
        created_at=NOW,
        expires_at=NOW,
        channel_id=0,
        message_id=0,
    )
    await cog._close_announcement(find, "note")
    cog.bot.get_channel.assert_not_called()


async def test_close_announcement_noop_when_channel_missing():
    cog = make_cog()
    cog.bot.get_channel.return_value = None
    find = NightFind(
        guild_id=10,
        location_id="x",
        item_id="y",
        created_at=NOW,
        expires_at=NOW,
        channel_id=1,
        message_id=2,
    )
    await cog._close_announcement(find, "note")


async def test_close_announcement_appends_note_to_embed():
    cog = make_cog()
    channel = MagicMock()
    message = MagicMock()
    embed = SimpleNamespace(description="исходное")
    message.embeds = [embed]
    message.edit = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=message)
    cog.bot.get_channel.return_value = channel
    find = NightFind(
        guild_id=10,
        location_id="x",
        item_id="y",
        created_at=NOW,
        expires_at=NOW,
        channel_id=1,
        message_id=2,
    )
    await cog._close_announcement(find, "-# опоздали")
    assert embed.description == "исходное\n\n-# опоздали"
    message.edit.assert_awaited_once_with(embed=embed, view=None)


async def test_close_announcement_no_embed_edits_with_none():
    cog = make_cog()
    channel = MagicMock()
    message = MagicMock()
    message.embeds = []
    message.edit = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=message)
    cog.bot.get_channel.return_value = channel
    find = NightFind(
        guild_id=10,
        location_id="x",
        item_id="y",
        created_at=NOW,
        expires_at=NOW,
        channel_id=1,
        message_id=2,
    )
    await cog._close_announcement(find, "note")
    message.edit.assert_awaited_once_with(embed=None, view=None)


async def test_close_announcement_http_exception_ignored():
    cog = make_cog()
    channel = MagicMock()
    channel.fetch_message = AsyncMock(side_effect=http_error())
    cog.bot.get_channel.return_value = channel
    find = NightFind(
        guild_id=10,
        location_id="x",
        item_id="y",
        created_at=NOW,
        expires_at=NOW,
        channel_id=1,
        message_id=2,
    )
    await cog._close_announcement(find, "note")  # не падает


# --- _announce_claim: недостающие ветки --------------------------------------


async def test_announce_claim_embed_present_appends_note():
    cog = make_cog()
    interaction = claim_interaction()
    embed = SimpleNamespace(description="исходное")
    interaction.message.embeds = [embed]
    result = ClaimResult(status="success", item=COMMON, points_delta=5, points_total=10, level=1)
    await cog._announce_claim(interaction, result)
    assert "Забрал" in embed.description
    interaction.message.edit.assert_awaited_once()


async def test_announce_claim_message_edit_http_exception_ignored():
    cog = make_cog()
    interaction = claim_interaction()
    interaction.message.edit = AsyncMock(side_effect=http_error())
    result = ClaimResult(status="success", item=COMMON, points_delta=5, points_total=10, level=1)
    await cog._announce_claim(interaction, result)
    interaction.channel.send.assert_awaited_once()  # публичный анонс всё равно уходит


async def test_announce_claim_legendary_line():
    cog = make_cog()
    interaction = claim_interaction()
    result = ClaimResult(status="success", item=LEGENDARY, points_delta=5, points_total=10, level=1)
    await cog._announce_claim(interaction, result)
    embed = interaction.channel.send.await_args.kwargs["embed"]
    assert _SUCCESS_LEGENDARY in embed.description


async def test_announce_claim_high_level_line():
    cog = make_cog()
    interaction = claim_interaction()
    result = ClaimResult(status="success", item=COMMON, points_delta=5, points_total=10, level=6)
    await cog._announce_claim(interaction, result)
    embed = interaction.channel.send.await_args.kwargs["embed"]
    assert _SUCCESS_HIGH in embed.description


async def test_announce_claim_mid_level_line():
    cog = make_cog()
    interaction = claim_interaction()
    result = ClaimResult(status="success", item=COMMON, points_delta=5, points_total=10, level=3)
    await cog._announce_claim(interaction, result)
    embed = interaction.channel.send.await_args.kwargs["embed"]
    assert _SUCCESS_MID in embed.description


async def test_announce_claim_low_level_line():
    cog = make_cog()
    interaction = claim_interaction()
    result = ClaimResult(status="success", item=COMMON, points_delta=5, points_total=10, level=1)
    await cog._announce_claim(interaction, result)
    embed = interaction.channel.send.await_args.kwargs["embed"]
    assert _SUCCESS_LOW in embed.description


async def test_announce_claim_public_send_http_exception_logged():
    cog = make_cog()
    interaction = claim_interaction()
    interaction.channel.send = AsyncMock(side_effect=http_error())
    result = ClaimResult(status="success", item=COMMON, points_delta=5, points_total=10, level=1)
    await cog._announce_claim(interaction, result)  # не падает


async def test_find_claim_view_button_delegates_to_handle_claim():
    cog = make_cog()
    cog.handle_claim = AsyncMock()
    view = FindClaimView(cog)
    interaction = make_interaction()
    await view.claim_button.callback(interaction)
    cog.handle_claim.assert_awaited_once_with(interaction)
