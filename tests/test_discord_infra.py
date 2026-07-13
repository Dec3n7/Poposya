"""Инфраструктура Discord, тестируемая без живого подключения: голосовое
соединение, синхронизация ролей, health-хендлер, сессия, доп. ветки outbox."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aiohttp.test_utils import make_mocked_request

from src.infrastructure.discord.voice import DiscordVoiceConnection
from src.infrastructure.discord.role_sync import RoleSyncService
from src.infrastructure.web.app import HealthChecker, create_web_app


def forbidden():
    return discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "no perms")


def http_error():
    return discord.HTTPException(MagicMock(status=500, reason="Server Error"), "boom")


# --- DiscordVoiceConnection -------------------------------------------------

def make_vc(playing=False, paused=False):
    vc = MagicMock()
    vc.is_playing.return_value = playing
    vc.is_paused.return_value = paused
    vc.disconnect = AsyncMock()
    return vc


def test_voice_pause_only_when_playing():
    vc = make_vc(playing=True)
    DiscordVoiceConnection(vc).pause()
    vc.pause.assert_called_once()

    vc2 = make_vc(playing=False)
    DiscordVoiceConnection(vc2).pause()
    vc2.pause.assert_not_called()


def test_voice_resume_only_when_paused():
    vc = make_vc(paused=True)
    DiscordVoiceConnection(vc).resume()
    vc.resume.assert_called_once()

    vc2 = make_vc(paused=False)
    DiscordVoiceConnection(vc2).resume()
    vc2.resume.assert_not_called()


def test_voice_stop_when_active():
    vc = make_vc(playing=True)
    DiscordVoiceConnection(vc).stop()
    vc.stop.assert_called_once()

    vc_idle = make_vc(playing=False, paused=False)
    DiscordVoiceConnection(vc_idle).stop()
    vc_idle.stop.assert_not_called()


def test_voice_set_volume_before_and_after_play():
    conn = DiscordVoiceConnection(make_vc())
    conn.set_volume(0.7)  # источника ещё нет — не падает
    conn._source = MagicMock()
    conn.set_volume(0.9)
    assert conn._source.volume == 0.9


async def test_voice_disconnect_forces():
    vc = make_vc()
    await DiscordVoiceConnection(vc).disconnect()
    vc.disconnect.assert_awaited_once_with(force=True)


async def test_voice_play_wraps_source(monkeypatch):
    vc = make_vc()
    conn = DiscordVoiceConnection(vc, ffmpeg_path="/usr/bin/ffmpeg")
    fake_audio = MagicMock()
    transformer = MagicMock()
    monkeypatch.setattr(discord, "FFmpegPCMAudio", MagicMock(return_value=fake_audio))
    monkeypatch.setattr(discord, "PCMVolumeTransformer", MagicMock(return_value=transformer))

    cb = lambda e: None
    await conn.play("https://stream.url/audio", 0.5, cb)
    discord.FFmpegPCMAudio.assert_called_once()
    # для http-стрима включаются reconnect-флаги
    _, kwargs = discord.FFmpegPCMAudio.call_args
    assert kwargs["before_options"] is not None
    vc.play.assert_called_once_with(transformer, after=cb)


async def test_voice_play_passes_headers_to_ffmpeg(monkeypatch):
    vc = make_vc()
    conn = DiscordVoiceConnection(vc)
    monkeypatch.setattr(discord, "FFmpegPCMAudio", MagicMock())
    monkeypatch.setattr(discord, "PCMVolumeTransformer", MagicMock())
    await conn.play("https://stream/audio", 0.5, lambda e: None,
                    headers={"User-Agent": "test-agent"})
    before = discord.FFmpegPCMAudio.call_args.kwargs["before_options"]
    assert "-headers" in before and "test-agent" in before


async def test_voice_play_local_file_no_reconnect(monkeypatch):
    vc = make_vc()
    conn = DiscordVoiceConnection(vc)
    monkeypatch.setattr(discord, "FFmpegPCMAudio", MagicMock())
    monkeypatch.setattr(discord, "PCMVolumeTransformer", MagicMock())
    await conn.play("/local/cache/track.webm", 0.5, lambda e: None)
    _, kwargs = discord.FFmpegPCMAudio.call_args
    assert kwargs["before_options"] is None  # локальному файлу reconnect не нужен


# --- RoleSyncService --------------------------------------------------------

def make_guild(role_names=(), members=None):
    roles = [SimpleNamespace(name=n) for n in role_names]
    guild = MagicMock()
    guild.id = 10
    guild.roles = roles
    guild.create_role = AsyncMock(side_effect=lambda name, reason=None: roles.append(
        SimpleNamespace(name=name)
    ))
    guild._members = members or {}
    guild.get_member = lambda uid: guild._members.get(uid)
    return guild


async def test_ensure_roles_creates_missing():
    guild = make_guild(role_names=["Знакомый"])
    svc = RoleSyncService(MagicMock(), ["Знакомый", "Друг", "Особенный"])
    await svc.ensure_roles(guild)
    names = {r.name for r in guild.roles}
    assert {"Знакомый", "Друг", "Особенный"} <= names
    # существовавшую роль не пересоздаём
    assert guild.create_role.await_count == 2


async def test_ensure_roles_stops_on_forbidden():
    guild = make_guild(role_names=[])
    guild.create_role = AsyncMock(side_effect=forbidden())
    svc = RoleSyncService(MagicMock(), ["A", "B"])
    await svc.ensure_roles(guild)  # не должно пробросить
    assert guild.create_role.await_count == 1  # после Forbidden выходим


async def test_sync_member_adds_desired_removes_others():
    role_a = SimpleNamespace(name="A")
    role_b = SimpleNamespace(name="B")
    member = MagicMock()
    member.roles = [role_a]  # сейчас на «A»
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    guild = make_guild(members={1: member})
    guild.roles = [role_a, role_b]

    svc = RoleSyncService(MagicMock(), ["A", "B"])
    await svc.sync_member(guild, 1, role_index=1)  # хотим «B»
    member.remove_roles.assert_awaited_once()
    member.add_roles.assert_awaited_once()
    assert member.add_roles.await_args.args[0] is role_b


async def test_sync_member_none_index_strips_all():
    role_a = SimpleNamespace(name="A")
    member = MagicMock()
    member.roles = [role_a]
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    guild = make_guild(members={1: member})
    guild.roles = [role_a]

    svc = RoleSyncService(MagicMock(), ["A"])
    await svc.sync_member(guild, 1, role_index=None)  # без статуса
    member.remove_roles.assert_awaited_once()
    member.add_roles.assert_not_awaited()


async def test_sync_member_fetch_when_not_cached():
    member = MagicMock()
    member.roles = []
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    guild = make_guild(members={})  # get_member -> None
    guild.fetch_member = AsyncMock(return_value=member)
    guild.roles = []
    svc = RoleSyncService(MagicMock(), ["A"])
    await svc.sync_member(guild, 1, role_index=None)
    guild.fetch_member.assert_awaited_once_with(1)


async def test_sync_member_gives_up_if_fetch_fails():
    guild = make_guild(members={})
    guild.fetch_member = AsyncMock(side_effect=http_error())
    svc = RoleSyncService(MagicMock(), ["A"])
    await svc.sync_member(guild, 1, role_index=0)  # не должно пробросить


# --- Health web handler -----------------------------------------------------

async def test_health_handler_healthy():
    checker = HealthChecker()
    checker.register("db", AsyncMock(return_value=True))
    app = create_web_app(checker)
    resp = await _call_health(app, checker)
    assert resp.status == 200


async def _call_health(app, checker):
    # прямой вызов зарегистрированного хендлера
    for route in app.router.routes():
        if route.resource.canonical == "/health":
            request = make_mocked_request("GET", "/health")
            return await route.handler(request)
    raise AssertionError("маршрут /health не найден")


async def test_health_handler_unhealthy():
    checker = HealthChecker()
    checker.register("db", AsyncMock(return_value=False))
    app = create_web_app(checker)
    resp = await _call_health(app, checker)
    assert resp.status == 503
