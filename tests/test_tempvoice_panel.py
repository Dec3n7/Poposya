"""Панель каморки: авторизация кнопок, тоглы, имя/лимит, участники, «Забрать»."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from src.application.persona.registry import PHRASE_SPECS
from src.application.tempvoice.use_cases import ClaimResult
from src.domain.tempvoice.entities import TempChannel
from src.infrastructure.discord.cogs.tempvoice.cog import TempVoiceCog
from src.infrastructure.discord.cogs.tempvoice.views import (
    TempVoicePanel,
    panel_embed,
    panel_state,
)

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
OWNER_ID = 1
STRANGER_ID = 2
CHANNEL_ID = 600
HUB_ID = 500


def _overwrite(connect=None, view_channel=None):
    return SimpleNamespace(connect=connect, view_channel=view_channel)


def _channel(members=(), name="Каморка Гость", user_limit=0, overwrite=None, channel_id=CHANNEL_ID):
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.id = channel_id
    channel.name = name
    channel.user_limit = user_limit
    channel.members = list(members)
    channel.guild = MagicMock()
    channel.guild.default_role = MagicMock()
    channel.overwrites_for = MagicMock(return_value=overwrite or _overwrite())
    channel.set_permissions = AsyncMock()
    channel.edit = AsyncMock()
    channel.send = AsyncMock()
    return channel


def _member(uid=OWNER_ID, is_bot=False, name="Гость"):
    member = MagicMock()
    member.id = uid
    member.bot = is_bot
    member.display_name = name
    member.move_to = AsyncMock()
    return member


def _interaction(user_id=OWNER_ID, channel=None, in_voice=None):
    """channel — где лежит нажатая панель; in_voice — где сидит нажавший
    (нужно только для панели хаба)."""
    interaction = MagicMock()
    interaction.channel = channel if channel is not None else _channel()
    interaction.channel_id = interaction.channel.id
    voice = SimpleNamespace(channel=in_voice) if in_voice is not None else None
    interaction.user = SimpleNamespace(id=user_id, voice=voice)
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def make_cog(owner_id=OWNER_ID, temp_ids=(CHANNEL_ID,)):
    """temp_ids — какие каналы БД считает каморками (хаб в них не входит)."""
    container = MagicMock()

    async def _get(channel_id):
        return TempChannel(10, channel_id, owner_id, NOW) if channel_id in temp_ids else None

    container.get.execute = AsyncMock(side_effect=_get)
    container.claim.execute = AsyncMock(return_value=ClaimResult(True, "", OWNER_ID))
    container.release.execute = AsyncMock()
    settings = SimpleNamespace(
        tempvoice_hub_channel=500,
        tempvoice_category=0,
        tempvoice_max_per_guild=25,
        tempvoice_default_limit=0,
    )
    return TempVoiceCog(MagicMock(), container, settings), container


# --- состояние и embed ---


def test_panel_state_reads_overwrites():
    assert panel_state(_channel(overwrite=_overwrite())) == (False, False)
    assert panel_state(_channel(overwrite=_overwrite(connect=False))) == (True, False)
    assert panel_state(_channel(overwrite=_overwrite(view_channel=False))) == (False, True)


def test_embed_shows_current_state():
    channel = _channel(user_limit=4, overwrite=_overwrite(connect=False))
    embed = panel_embed(channel, OWNER_ID)
    values = {f.name: f.value for f in embed.fields}
    assert "закрыта" in values["Дверь"]
    assert values["Мест"] == "4"
    assert values["Хозяин"] == f"<@{OWNER_ID}>"


def test_embed_no_limit_is_readable():
    values = {f.name: f.value for f in panel_embed(_channel(), OWNER_ID).fields}
    assert values["Мест"] == "без лимита"


def test_panel_buttons_are_persistent():
    panel = TempVoicePanel()
    assert panel.timeout is None
    ids = [item.custom_id for item in panel.children]
    assert ids == [
        "tv:lock",
        "tv:hide",
        "tv:name",
        "tv:limit",
        "tv:kick",
        "tv:permit",
        "tv:block",
        "tv:claim",
    ]


def test_toggle_labels_follow_state():
    open_panel = {i.custom_id: i for i in TempVoicePanel(locked=False, hidden=False).children}
    assert open_panel["tv:lock"].label == "Закрыть"
    locked_panel = {i.custom_id: i for i in TempVoicePanel(locked=True, hidden=True).children}
    assert locked_panel["tv:lock"].label == "Открыть"  # кнопка говорит, что сделает
    assert locked_panel["tv:hide"].label == "Показать"


# --- авторизация ---


async def test_stranger_gets_pointed_at_claim():
    cog, _ = make_cog(owner_id=OWNER_ID)
    interaction = _interaction(user_id=STRANGER_ID)
    await cog.on_lock(interaction)
    text = interaction.response.send_message.await_args.args[0]
    assert "Забрать" in text  # отказ объясняет, что делать
    interaction.channel.set_permissions.assert_not_awaited()


async def test_button_outside_temp_without_voice_refused():
    # панель не в каморке и человек не в войсе — действовать не на что
    cog, _ = make_cog(temp_ids=())
    interaction = _interaction()
    await cog.on_lock(interaction)
    assert interaction.response.send_message.await_args.args[0] == PHRASE_SPECS["tempvoice.not_in_voice"].default


# --- замок и видимость ---


async def test_lock_closes_door_and_redraws():
    cog, _ = make_cog()
    channel = _channel(overwrite=_overwrite())
    interaction = _interaction(channel=channel)
    await cog.on_lock(interaction)
    overwrite = channel.set_permissions.await_args.kwargs["overwrite"]
    assert overwrite.connect is False
    interaction.response.edit_message.assert_awaited_once()


async def test_unlock_restores_inheritance_not_grant():
    """Открывая, возвращаем None (наследование), а не True — иначе выдали бы
    право поверх серверных настроек."""
    cog, _ = make_cog()
    channel = _channel(overwrite=_overwrite(connect=False))
    await cog.on_lock(_interaction(channel=channel))
    assert channel.set_permissions.await_args.kwargs["overwrite"].connect is None


async def test_hide_and_show():
    cog, _ = make_cog()
    channel = _channel(overwrite=_overwrite())
    await cog.on_hide(_interaction(channel=channel))
    assert channel.set_permissions.await_args.kwargs["overwrite"].view_channel is False
    channel = _channel(overwrite=_overwrite(view_channel=False))
    await cog.on_hide(_interaction(channel=channel))
    assert channel.set_permissions.await_args.kwargs["overwrite"].view_channel is None


async def test_permission_failure_is_explained():
    cog, _ = make_cog()
    channel = _channel()
    channel.set_permissions = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
    interaction = _interaction(channel=channel)
    await cog.on_lock(interaction)
    assert interaction.response.send_message.await_args.args[0] == PHRASE_SPECS["tempvoice.action_failed"].default
    interaction.response.edit_message.assert_not_awaited()


# --- имя ---


async def test_rename_opens_modal_with_current_name():
    cog, _ = make_cog()
    interaction = _interaction(channel=_channel(name="Каморка Гость"))
    await cog.on_rename(interaction)
    modal = interaction.response.send_modal.await_args.args[0]
    assert modal.name.default == "Каморка Гость"


async def test_rename_applies_and_redraws():
    cog, _ = make_cog()
    interaction = _interaction()
    await cog.apply_name(interaction, "Подвал")
    assert interaction.channel.edit.await_args.kwargs["name"] == "Подвал"
    interaction.response.edit_message.assert_awaited_once()


async def test_third_rename_refused_with_wait_time():
    """Discord: 2 переименования за 10 минут. Третье не вешаем в ожидание —
    честно говорим, сколько ждать."""
    cog, _ = make_cog()
    await cog.apply_name(_interaction(), "Раз")
    await cog.apply_name(_interaction(), "Два")
    interaction = _interaction()
    await cog.on_rename(interaction)
    interaction.response.send_modal.assert_not_awaited()
    text = interaction.response.send_message.await_args.args[0]
    assert "через" in text and "сек" in text


async def test_rename_counter_is_per_channel():
    cog, _ = make_cog(temp_ids=(CHANNEL_ID, 601))
    await cog.apply_name(_interaction(), "Раз")
    await cog.apply_name(_interaction(), "Два")
    other = _interaction(channel=_channel(channel_id=601))  # другая каморка — свой счётчик
    await cog.on_rename(other)
    other.response.send_modal.assert_awaited_once()


# --- лимит ---


async def test_limit_applied():
    cog, _ = make_cog()
    interaction = _interaction()
    await cog.apply_limit(interaction, "5")
    assert interaction.channel.edit.await_args.kwargs["user_limit"] == 5


async def test_limit_rejects_garbage_and_out_of_range():
    cog, _ = make_cog()
    for raw in ("много", "-1", "100"):
        interaction = _interaction()
        await cog.apply_limit(interaction, raw)
        assert interaction.response.send_message.await_args.args[0] == PHRASE_SPECS["tempvoice.limit_bad"].default
        interaction.channel.edit.assert_not_awaited()


async def test_limit_zero_means_no_limit():
    cog, _ = make_cog()
    interaction = _interaction()
    await cog.apply_limit(interaction, "0")
    assert interaction.channel.edit.await_args.kwargs["user_limit"] == 0


# --- участники ---


async def test_kick_moves_member_out():
    cog, _ = make_cog()
    target = _member(uid=STRANGER_ID)
    channel = _channel(members=[target])
    interaction = _interaction(channel=channel)
    await cog.apply_member_action(interaction, "kick", target)
    target.move_to.assert_awaited_once_with(None, reason="Каморка: выгнал владелец")


async def test_kick_absent_member_says_so():
    cog, _ = make_cog()
    target = _member(uid=STRANGER_ID)
    interaction = _interaction(channel=_channel(members=[]))
    await cog.apply_member_action(interaction, "kick", target)
    target.move_to.assert_not_awaited()
    assert interaction.response.send_message.await_args.args[0] == PHRASE_SPECS["tempvoice.not_here"].default


async def test_permit_grants_connect():
    cog, _ = make_cog()
    target = _member(uid=STRANGER_ID)
    interaction = _interaction()
    await cog.apply_member_action(interaction, "permit", target)
    kwargs = interaction.channel.set_permissions.await_args.kwargs
    assert kwargs["connect"] is True and kwargs["view_channel"] is True


async def test_block_denies_and_evicts():
    cog, _ = make_cog()
    target = _member(uid=STRANGER_ID)
    channel = _channel(members=[target])
    await cog.apply_member_action(_interaction(channel=channel), "block", target)
    kwargs = channel.set_permissions.await_args.kwargs
    assert kwargs["connect"] is False
    target.move_to.assert_awaited_once()  # был внутри — выставили


async def test_block_absent_member_only_denies():
    cog, _ = make_cog()
    target = _member(uid=STRANGER_ID)
    channel = _channel(members=[])
    await cog.apply_member_action(_interaction(channel=channel), "block", target)
    channel.set_permissions.assert_awaited_once()
    target.move_to.assert_not_awaited()


async def test_cannot_target_self():
    cog, _ = make_cog()
    owner = _member(uid=OWNER_ID)
    interaction = _interaction(user_id=OWNER_ID)
    await cog.apply_member_action(interaction, "kick", owner)
    owner.move_to.assert_not_awaited()
    assert interaction.response.send_message.await_args.args[0] == PHRASE_SPECS["tempvoice.self_target"].default


# --- «Забрать» ---


async def test_claim_passes_only_humans_as_present():
    cog, container = make_cog()
    channel = _channel(members=[_member(uid=STRANGER_ID), _member(uid=99, is_bot=True)])
    await cog.on_claim(_interaction(user_id=STRANGER_ID, channel=channel))
    args = container.claim.execute.await_args.args
    assert args[2] == {STRANGER_ID}  # Попося с музыкой не «присутствует»


async def test_claim_success_redraws_with_new_owner():
    cog, container = make_cog()
    container.claim.execute = AsyncMock(return_value=ClaimResult(True, "", OWNER_ID))
    interaction = _interaction(user_id=STRANGER_ID)
    await cog.on_claim(interaction)
    interaction.response.edit_message.assert_awaited_once()
    embed = interaction.response.edit_message.await_args.kwargs["embed"]
    owner_field = {f.name: f.value for f in embed.fields}["Хозяин"]
    assert owner_field == f"<@{STRANGER_ID}>"


async def test_claim_refusal_explains_reason():
    cog, container = make_cog()
    container.claim.execute = AsyncMock(return_value=ClaimResult(False, "owner_present", OWNER_ID))
    interaction = _interaction(user_id=STRANGER_ID)
    await cog.on_claim(interaction)
    assert (
        interaction.response.send_message.await_args.args[0]
        == PHRASE_SPECS["tempvoice.claim_refusals"].default["owner_present"]
    )
    interaction.response.edit_message.assert_not_awaited()


async def test_claim_is_the_only_button_open_to_non_owner():
    # не владелец: claim идёт в use case, остальные кнопки — отказ
    cog, container = make_cog()
    await cog.on_claim(_interaction(user_id=STRANGER_ID))
    container.claim.execute.assert_awaited_once()


# --- панель хаба: одна на всех, цель — твой текущий войс ---


async def test_hub_button_acts_on_your_current_room():
    cog, _ = make_cog()
    my_room = _channel(overwrite=_overwrite())
    hub = _channel(channel_id=HUB_ID, name="➕ Создать канал")
    await cog.on_lock(_interaction(channel=hub, in_voice=my_room))
    # тронули каморку, а не хаб
    my_room.set_permissions.assert_awaited_once()
    hub.set_permissions.assert_not_awaited()


async def test_hub_button_answers_ephemerally_never_edits_shared_panel():
    """Панель хаба общая: перерисовать её под одного — показать состояние
    его каморки всему серверу."""
    cog, _ = make_cog()
    interaction = _interaction(channel=_channel(channel_id=HUB_ID), in_voice=_channel())
    await cog.on_lock(interaction)
    interaction.response.edit_message.assert_not_awaited()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert "Дверь" in {f.name for f in kwargs["embed"].fields}  # состояние всё равно видно


async def test_hub_button_without_voice_explains():
    cog, _ = make_cog()
    interaction = _interaction(channel=_channel(channel_id=HUB_ID), in_voice=None)
    await cog.on_lock(interaction)
    assert interaction.response.send_message.await_args.args[0] == PHRASE_SPECS["tempvoice.not_in_voice"].default


async def test_hub_button_from_ordinary_voice_explains():
    cog, _ = make_cog(temp_ids=(CHANNEL_ID,))
    ordinary = _channel(channel_id=777)  # обычный войс, не каморка
    interaction = _interaction(channel=_channel(channel_id=HUB_ID), in_voice=ordinary)
    await cog.on_lock(interaction)
    assert interaction.response.send_message.await_args.args[0] == PHRASE_SPECS["tempvoice.not_in_temp"].default


async def test_hub_button_respects_ownership():
    cog, _ = make_cog(owner_id=OWNER_ID)
    interaction = _interaction(
        user_id=STRANGER_ID, channel=_channel(channel_id=HUB_ID), in_voice=_channel()
    )
    await cog.on_lock(interaction)
    assert "Забрать" in interaction.response.send_message.await_args.args[0]


async def test_local_panel_still_edited_in_place():
    # в самой каморке панель личная — её перерисовываем, а не шлём эфемерку
    cog, _ = make_cog()
    interaction = _interaction(channel=_channel(overwrite=_overwrite()))
    await cog.on_lock(interaction)
    interaction.response.edit_message.assert_awaited_once()


async def test_rename_from_hub_renames_your_room():
    cog, _ = make_cog()
    my_room = _channel()
    await cog.apply_name(
        _interaction(channel=_channel(channel_id=HUB_ID), in_voice=my_room), "Нора"
    )
    assert my_room.edit.await_args.kwargs["name"] == "Нора"


# --- восстановление панели хаба ---


def _hub_with_history(messages, channel_id=HUB_ID):
    hub = _channel(channel_id=channel_id)

    async def _history(limit=20):
        for message in messages:
            yield message

    hub.history = _history
    return hub


def _panel_message(bot_id=42):
    message = MagicMock()
    message.author.id = bot_id
    button = SimpleNamespace(custom_id="tv:lock")
    message.components = [SimpleNamespace(children=[button])]
    return message


def _chatter(user_id=7):
    message = MagicMock()
    message.author.id = user_id
    message.components = []
    return message


def _guild_with_hub(hub, guild_id=10):
    guild = MagicMock()
    guild.id = guild_id
    guild.get_channel = MagicMock(return_value=hub)
    return guild


async def test_hub_panel_posted_when_absent():
    cog, _ = make_cog()
    cog.bot.user.id = 42
    hub = _hub_with_history([_chatter(), _chatter()])
    await cog._ensure_hub_panel(_guild_with_hub(hub))
    hub.send.assert_awaited_once()
    assert hub.send.await_args.kwargs["embed"].title == PHRASE_SPECS["tempvoice.hub_title"].default


async def test_hub_panel_not_duplicated_when_present():
    cog, _ = make_cog()
    cog.bot.user.id = 42
    hub = _hub_with_history([_chatter(), _panel_message(bot_id=42)])
    await cog._ensure_hub_panel(_guild_with_hub(hub))
    hub.send.assert_not_awaited()


async def test_hub_panel_checked_once_per_hub():
    cog, _ = make_cog()
    cog.bot.user.id = 42
    hub = _hub_with_history([])
    guild = _guild_with_hub(hub)
    await cog._ensure_hub_panel(guild)
    await cog._ensure_hub_panel(guild)
    hub.send.assert_awaited_once()  # второй раз историю не читаем


async def test_changing_hub_puts_panel_in_new_one():
    """Сменили хаб через /config — панель должна появиться и без рестарта."""
    cog, _ = make_cog()
    cog.bot.user.id = 42
    old_hub = _hub_with_history([])
    await cog._ensure_hub_panel(_guild_with_hub(old_hub))
    cog.settings.tempvoice_hub_channel = 501  # админ переназначил
    new_hub = _hub_with_history([], channel_id=501)
    await cog._ensure_hub_panel(_guild_with_hub(new_hub))
    new_hub.send.assert_awaited_once()


async def test_hub_panel_skipped_when_feature_off():
    cog, _ = make_cog()
    cog.settings.tempvoice_hub_channel = 0
    guild = _guild_with_hub(_hub_with_history([]))
    await cog._ensure_hub_panel(guild)
    guild.get_channel.assert_not_called()


async def test_hub_panel_failure_allows_retry():
    # не смогли выложить — не запираем себя навсегда
    cog, _ = make_cog()
    cog.bot.user.id = 42
    hub = _hub_with_history([])
    hub.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
    await cog._ensure_hub_panel(_guild_with_hub(hub))
    assert HUB_ID not in cog._panelled  # следующий вход попробует снова


async def test_rename_limit_from_hub_counts_the_room_not_the_hub():
    """Из хаба переименования должны ложиться на счётчик каморки, а не хаба:
    иначе он общий на всех, а лимит Discord — на конкретный канал."""
    cog, _ = make_cog(temp_ids=(CHANNEL_ID, 601))
    hub = _channel(channel_id=HUB_ID)
    room_a = _channel(channel_id=CHANNEL_ID)
    room_b = _channel(channel_id=601)
    await cog.apply_name(_interaction(channel=hub, in_voice=room_a), "Раз")
    await cog.apply_name(_interaction(channel=hub, in_voice=room_a), "Два")

    third = _interaction(channel=hub, in_voice=room_a)
    await cog.on_rename(third)
    third.response.send_modal.assert_not_awaited()  # своё окно исчерпано

    other = _interaction(channel=hub, in_voice=room_b)
    await cog.on_rename(other)
    other.response.send_modal.assert_awaited_once()  # у чужой каморки счётчик свой
