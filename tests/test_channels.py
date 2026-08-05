"""Резолвер каналов: first_postable (system → фолбэк), resolve_channel
(ID → имя → фолбэк/None), same_channel."""

from unittest.mock import MagicMock

import discord

from src.infrastructure.discord.channels import (
    first_postable,
    is_designated_main,
    resolve_channel,
)


def _chan(cid=1, name="", send=True, view=True):
    ch = MagicMock(spec=discord.TextChannel)
    ch.id = cid
    ch.name = name
    ch.permissions_for.return_value = MagicMock(send_messages=send, view_channel=view)
    return ch


def _guild(system=None, text=(), me="me", get=None):
    guild = MagicMock(spec=discord.Guild)
    guild.me = me
    guild.system_channel = system
    guild.text_channels = list(text)
    guild.get_channel = MagicMock(return_value=get)
    return guild


# --- first_postable ---------------------------------------------------------


def test_first_postable_prefers_system():
    system = _chan(send=True)
    assert first_postable(_guild(system=system, text=[_chan(cid=2)])) is system


def test_first_postable_skips_unpostable_system():
    fallback = _chan(cid=2, send=True)
    guild = _guild(system=_chan(cid=1, send=False), text=[fallback])
    assert first_postable(guild) is fallback


def test_first_postable_none_when_nothing_postable():
    guild = _guild(system=_chan(send=False), text=[_chan(cid=2, send=False)])
    assert first_postable(guild) is None


def test_first_postable_none_without_me():
    assert first_postable(_guild(me=None)) is None


# --- resolve_channel --------------------------------------------------------


def test_resolve_prefers_configured_id():
    target = _chan(cid=555, name="whatever")
    guild = _guild(get=target)
    assert resolve_channel(guild, 555, "имя", fallback=True) is target


def test_resolve_falls_back_to_legacy_name():
    named = _chan(cid=1, name="основной")
    guild = _guild(text=[named], get=None)
    assert resolve_channel(guild, 0, "основной", fallback=True) is named


def test_resolve_fallback_to_system_when_name_missing():
    system = _chan(cid=9, name="sys", send=True)
    guild = _guild(system=system, text=[system], get=None)
    assert resolve_channel(guild, 0, "нет-такого", fallback=True) is system


def test_resolve_none_when_no_fallback():
    guild = _guild(system=_chan(send=True), text=[], get=None)
    assert resolve_channel(guild, 0, "нет-такого", fallback=False) is None


def test_resolve_ignores_non_text_channel_id():
    # get_channel вернул не текстовый канал (напр. категорию) — игнорируем, идём к имени
    named = _chan(cid=1, name="основной")
    guild = _guild(text=[named], get=object())
    assert resolve_channel(guild, 777, "основной", fallback=True) is named


# --- is_designated_main -----------------------------------------------------


def test_designated_main_by_id_takes_priority():
    assert is_designated_main(_chan(cid=5, name="что-угодно"), 5, "имя") is True
    # ID задан, но не совпал — имя уже не смотрим
    assert is_designated_main(_chan(cid=9, name="имя"), 5, "имя") is False


def test_designated_main_by_name_when_no_id():
    assert is_designated_main(_chan(cid=1, name="основной"), 0, "основной") is True
    assert is_designated_main(_chan(cid=1, name="прочее"), 0, "основной") is False


def test_designated_main_none_channel():
    assert is_designated_main(None, 0, "основной") is False
