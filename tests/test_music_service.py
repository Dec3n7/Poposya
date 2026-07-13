"""MusicPlayerService (build_embed, session/player доступ, handle_voice_state,
cleanup) и RadioService — с фейками discord/контейнера, без сети."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.music.player import GuildPlayer
from src.domain.music.entities import RepeatMode, Track
from src.infrastructure.discord.cogs.music.radio import RadioService
from src.infrastructure.discord.cogs.music.service import MusicPlayerService
from src.infrastructure.discord.cogs.music.session import GuildMusicSession


def make_track(vid="a", title="Песня", duration=200, requested_by=1):
    return Track(video_id=vid, title=title, url=f"https://youtu.be/{vid}",
                 duration=duration, requested_by=requested_by, thumbnail="http://t/1.jpg")


def make_settings():
    return SimpleNamespace(
        ffmpeg_path="ffmpeg", music_default_volume=0.5, music_prefetch_tracks=3,
        music_progress_interval=5, music_idle_timeout=60, music_idle_warn_seconds=10,
    )


def make_container():
    return SimpleNamespace(
        settings=make_settings(),
        audio_source=MagicMock(),
        event_bus=MagicMock(),
    )


def make_service():
    bot = MagicMock()
    bot.user = SimpleNamespace(id=999)
    return MusicPlayerService(bot, make_container())


def make_player(svc, current=None, queue=(), volume=0.5, paused=False):
    voice = MagicMock()
    player = GuildPlayer(
        guild_id=10, audio_source=svc.audio, voice=voice,
        event_bus=svc.bus, volume=volume,
    )
    player.current = current
    for t in queue:
        player.queue.append(t)
    player.is_paused = paused
    return player


# --- build_embed ------------------------------------------------------------

def test_build_embed_idle():
    svc = make_service()
    embed = svc.build_embed(make_player(svc))
    assert "Очередь пуста" in embed.description


def test_build_embed_playing_with_queue():
    svc = make_service()
    player = make_player(
        svc, current=make_track("a", "Текущая"),
        queue=[make_track("b", "Следующая"), make_track("c", "Третья")],
        volume=0.8,
    )
    embed = svc.build_embed(player)
    assert "Текущая" in embed.description
    field_names = [f.name for f in embed.fields]
    assert "Громкость" in field_names and "Далее" in field_names
    vol_field = next(f for f in embed.fields if f.name == "Громкость")
    assert vol_field.value == "80%"


def test_build_embed_shows_uploader():
    svc = make_service()
    svc.audio.track_meta = MagicMock(return_value=None)
    track = Track(video_id="a", title="Песня", url="u", duration=200,
                  requested_by=1, uploader="Крутой Артист")
    embed = svc.build_embed(make_player(svc, current=track))
    assert "Крутой Артист" in embed.description


def test_build_embed_shows_metadata():
    svc = make_service()
    svc.audio.track_meta = MagicMock(return_value={
        "view_count": 1_234_567, "upload_date": "20190615"})
    track = Track(video_id="a", title="Песня", url="u", duration=200,
                  requested_by=1, uploader="Артист")
    embed = svc.build_embed(make_player(svc, current=track))
    assert "1.2M" in embed.description   # просмотры
    assert "2019" in embed.description   # год


def test_build_embed_live_stream():
    svc = make_service()
    player = make_player(svc, current=make_track("l", "Эфир", duration=None))
    embed = svc.build_embed(player)
    assert "Прямой эфир" in embed.description


def test_build_embed_paused_title():
    svc = make_service()
    player = make_player(svc, current=make_track(), paused=True)
    embed = svc.build_embed(player)
    assert "Пауза" in embed.title


def test_build_embed_queue_overflow_shows_rest():
    svc = make_service()
    queue = [make_track(str(i), f"T{i}") for i in range(6)]
    player = make_player(svc, current=make_track("cur"), queue=queue)
    embed = svc.build_embed(player)
    dalee = next(f for f in embed.fields if f.name == "Далее")
    assert "и ещё 3" in dalee.value  # показаны 3, ещё 3 в остатке


def test_build_embed_radio_badge():
    svc = make_service()
    radio = MagicMock()
    radio.is_enabled.return_value = True
    svc.radio = radio
    player = make_player(svc, current=make_track())
    embed = svc.build_embed(player)
    assert any(f.name == "Радио" for f in embed.fields)


# --- get_session / get_player ----------------------------------------------

def test_get_session_and_player():
    svc = make_service()
    assert svc.get_session(10) is None
    assert svc.get_player(10) is None
    player = make_player(svc)
    svc.sessions[10] = GuildMusicSession(player=player)
    assert svc.get_session(10) is not None
    assert svc.get_player(10) is player


# --- cancel_idle ------------------------------------------------------------

def test_cancel_idle_no_session():
    svc = make_service()
    svc.cancel_idle(10)  # не падает


# --- handle_voice_state -----------------------------------------------------

async def test_handle_voice_state_bot_kicked_cleans_up():
    svc = make_service()
    svc.cleanup = AsyncMock()
    svc.sessions[10] = GuildMusicSession(player=make_player(svc))

    member = MagicMock()
    member.id = 999  # это сам бот
    member.guild = SimpleNamespace(id=10, voice_client=None)
    before = SimpleNamespace(channel=MagicMock())
    after = SimpleNamespace(channel=None)
    await svc.handle_voice_state(member, before, after)
    svc.cleanup.assert_awaited_once()


async def test_handle_voice_state_last_human_starts_grace_not_instant_leave():
    svc = make_service()
    svc.cleanup = AsyncMock()
    svc.sessions[10] = GuildMusicSession(player=make_player(svc))

    bot_member = SimpleNamespace(bot=True)
    channel = SimpleNamespace(members=[bot_member])  # людей не осталось
    vc = SimpleNamespace(channel=channel)
    member = MagicMock()
    member.id = 1
    member.bot = False
    member.guild = SimpleNamespace(id=10, voice_client=vc)
    state = SimpleNamespace(channel=None)
    await svc.handle_voice_state(member, state, state)
    # не выходим сразу — заведён грейс-таймер
    svc.cleanup.assert_not_awaited()
    assert 10 in svc._empty_grace
    svc._cancel_empty_grace(10)  # прибрать таймер


async def test_handle_voice_state_return_cancels_grace():
    svc = make_service()
    svc.sessions[10] = GuildMusicSession(player=make_player(svc))
    human = SimpleNamespace(bot=False)
    vc = SimpleNamespace(channel=SimpleNamespace(members=[human]))
    member = MagicMock()
    member.id = 1
    member.bot = False
    member.guild = SimpleNamespace(id=10, voice_client=vc)
    # заранее «висит» грейс-таймер
    import asyncio
    svc._empty_grace[10] = asyncio.get_event_loop().create_task(asyncio.sleep(999))
    state = SimpleNamespace(channel=vc.channel)
    await svc.handle_voice_state(member, state, state)
    assert 10 not in svc._empty_grace  # человек вернулся — таймер снят


async def test_empty_countdown_leaves_if_still_empty():
    svc = make_service()
    svc.cleanup = AsyncMock()
    vc = SimpleNamespace(channel=SimpleNamespace(members=[SimpleNamespace(bot=True)]))
    svc.bot.get_guild = MagicMock(return_value=SimpleNamespace(voice_client=vc))
    import src.infrastructure.discord.cogs.music.service as mod
    orig = mod._EMPTY_GRACE_SECONDS
    mod._EMPTY_GRACE_SECONDS = 0
    try:
        await svc._empty_countdown(10)
    finally:
        mod._EMPTY_GRACE_SECONDS = orig
    svc.cleanup.assert_awaited_once()


async def test_handle_voice_state_ignores_other_bots():
    svc = make_service()
    svc.cleanup = AsyncMock()
    member = MagicMock()
    member.id = 5
    member.bot = True
    member.guild = SimpleNamespace(id=10, voice_client=None)
    state = SimpleNamespace(channel=None)
    await svc.handle_voice_state(member, state, state)
    svc.cleanup.assert_not_awaited()


# --- cleanup ----------------------------------------------------------------

async def test_cleanup_stops_player_and_edits_message():
    svc = make_service()
    player = make_player(svc, current=make_track())
    player.stop_and_clear = AsyncMock()
    message = MagicMock()
    message.edit = AsyncMock()
    svc.sessions[10] = GuildMusicSession(player=player, message=message)

    await svc.cleanup(10, "Пока.")
    assert 10 not in svc.sessions
    player.stop_and_clear.assert_awaited_once()
    message.edit.assert_awaited_once()


async def test_cleanup_missing_session_is_noop():
    svc = make_service()
    await svc.cleanup(10, "reason")  # не падает


# --- rich presence ----------------------------------------------------------

async def test_presence_set_when_playing():
    svc = make_service()
    svc.bot.change_presence = AsyncMock()
    svc.sessions[10] = GuildMusicSession(player=make_player(svc, current=make_track(title="Хит")))
    await svc.refresh_presence()
    svc.bot.change_presence.assert_awaited_once()
    activity = svc.bot.change_presence.await_args.kwargs["activity"]
    assert activity.name == "Хит"


async def test_presence_cleared_when_silent():
    svc = make_service()
    svc.bot.change_presence = AsyncMock()
    svc._presence_name = "Старый"  # будто что-то играло
    await svc.refresh_presence()  # сессий нет -> сброс
    assert svc.bot.change_presence.await_args.kwargs["activity"] is None


async def test_presence_skips_redundant_update():
    svc = make_service()
    svc.bot.change_presence = AsyncMock()
    svc.sessions[10] = GuildMusicSession(player=make_player(svc, current=make_track(title="Хит")))
    await svc.refresh_presence()
    await svc.refresh_presence()  # то же самое — второй раз не дёргаем API
    assert svc.bot.change_presence.await_count == 1


# --- RadioService -----------------------------------------------------------

def make_radio(session=None, guild=None, container=None):
    bot = MagicMock()
    bot.user = SimpleNamespace(id=999)
    bot.get_guild.return_value = guild
    return RadioService(bot, container or MagicMock(), lambda gid: session)


def test_radio_toggle_and_enabled():
    radio = make_radio()
    assert radio.is_enabled(10) is False
    assert radio.toggle(10) is True
    assert radio.is_enabled(10) is True
    assert radio.toggle(10) is False


def test_radio_recently_filled(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr("src.infrastructure.discord.cogs.music.radio.time.monotonic",
                        lambda: clock["t"])
    radio = make_radio()
    assert radio.recently_filled(10) is False  # ни разу не заполняли
    radio._last_fill[10] = 1000.0
    clock["t"] = 1010.0
    assert radio.recently_filled(10) is True    # 10 c < 30 c
    clock["t"] = 1040.0
    assert radio.recently_filled(10) is False   # 40 c > 30 c


async def test_radio_fill_no_session_returns_false():
    radio = make_radio(session=None, guild=MagicMock())
    assert await radio.fill(10) is False


async def test_radio_fill_no_voice_client_returns_false():
    session = GuildMusicSession(player=MagicMock())
    guild = SimpleNamespace(voice_client=None)
    radio = make_radio(session=session, guild=guild)
    assert await radio.fill(10) is False


async def test_radio_fill_enqueues_liked_tracks():
    player = MagicMock()
    player.enqueue = AsyncMock()
    session = GuildMusicSession(player=player)

    human = SimpleNamespace(id=1, bot=False)
    channel = SimpleNamespace(members=[human, SimpleNamespace(id=2, bot=True)])
    guild = SimpleNamespace(voice_client=SimpleNamespace(channel=channel))

    container = MagicMock()
    liked = SimpleNamespace(
        video_id="v1",
        to_track=lambda uid: make_track("v1", requested_by=uid),
    )
    container.list_liked.execute = AsyncMock(return_value=[liked])

    radio = make_radio(session=session, guild=guild, container=container)
    assert await radio.fill(10) is True
    player.enqueue.assert_awaited_once()
    assert "v1" in radio._history[10]


async def test_radio_fill_falls_back_to_empty():
    player = MagicMock()
    player.enqueue = AsyncMock()
    session = GuildMusicSession(player=player)
    channel = SimpleNamespace(members=[])  # никого в войсе
    guild = SimpleNamespace(voice_client=SimpleNamespace(channel=channel))
    container = MagicMock()
    container.list_playlists.execute = AsyncMock(return_value=[])  # и плейлистов нет
    radio = make_radio(session=session, guild=guild, container=container)
    assert await radio.fill(10) is False
    player.enqueue.assert_not_awaited()
