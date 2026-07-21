"""MusicPlayerService (build_embed, session/player доступ, handle_voice_state,
cleanup, фоновые циклы обновления/простоя/пустого войса) и RadioService — с
фейками discord/контейнера, без сети."""

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.application.music.player import GuildPlayer
from src.domain.music.entities import Track
from src.infrastructure.discord.cogs.music import service as service_module
from src.infrastructure.discord.cogs.music.radio import RadioService
from src.infrastructure.discord.cogs.music.service import MusicPlayerService
from src.infrastructure.discord.cogs.music.session import GuildMusicSession
from tests.cog_fakes import http_error, not_found


def make_track(vid="a", title="Песня", duration=200, requested_by=1):
    return Track(
        video_id=vid,
        title=title,
        url=f"https://youtu.be/{vid}",
        duration=duration,
        requested_by=requested_by,
        thumbnail="http://t/1.jpg",
    )


def make_settings():
    return SimpleNamespace(
        ffmpeg_path="ffmpeg",
        music_default_volume=0.5,
        music_prefetch_tracks=3,
        music_progress_interval=5,
        music_idle_timeout=60,
        music_idle_warn_seconds=10,
    )


def make_container():
    return SimpleNamespace(
        settings=make_settings(),
        audio_source=MagicMock(),
        event_bus=MagicMock(),
    )


def make_service(settings=None):
    bot = MagicMock()
    bot.user = SimpleNamespace(id=999)
    container = SimpleNamespace(
        settings=settings or make_settings(),
        audio_source=MagicMock(),
        event_bus=MagicMock(),
    )
    return MusicPlayerService(bot, container)


def make_player(svc, current=None, queue=(), volume=0.5, paused=False):
    voice = MagicMock()
    player = GuildPlayer(
        guild_id=10,
        audio_source=svc.audio,
        voice=voice,
        event_bus=svc.bus,
        volume=volume,
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
        svc,
        current=make_track("a", "Текущая"),
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
    track = Track(
        video_id="a", title="Песня", url="u", duration=200, requested_by=1, uploader="Крутой Артист"
    )
    embed = svc.build_embed(make_player(svc, current=track))
    assert "Крутой Артист" in embed.description


def test_build_embed_shows_metadata():
    svc = make_service()
    svc.audio.track_meta = MagicMock(
        return_value={"view_count": 1_234_567, "upload_date": "20190615"}
    )
    track = Track(
        video_id="a", title="Песня", url="u", duration=200, requested_by=1, uploader="Артист"
    )
    embed = svc.build_embed(make_player(svc, current=track))
    assert "1.2M" in embed.description  # просмотры
    assert "2019" in embed.description  # год


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


async def test_presence_reports_playing_track():
    # refresh_presence отдаёт играющий трек владельцу presence (дедуп — там)
    svc = make_service()
    svc._presence = SimpleNamespace(set_now_playing=AsyncMock())
    svc.sessions[10] = GuildMusicSession(player=make_player(svc, current=make_track(title="Хит")))
    await svc.refresh_presence()
    svc._presence.set_now_playing.assert_awaited_once_with("Хит")


async def test_presence_reports_none_when_silent():
    svc = make_service()
    svc._presence = SimpleNamespace(set_now_playing=AsyncMock())
    await svc.refresh_presence()  # сессий нет -> None (владелец поставит «жизнь»)
    svc._presence.set_now_playing.assert_awaited_once_with(None)


# --- spawn / shutdown --------------------------------------------------------


async def test_spawn_holds_reference_until_task_completes():
    svc = make_service()
    done = asyncio.Event()

    async def coro():
        done.set()

    svc.spawn(coro())
    await asyncio.wait_for(done.wait(), timeout=1)
    await asyncio.sleep(0)  # дать done_callback снять задачу из набора
    assert svc._background == set()


async def test_shutdown_cancels_everything_and_cleans_sessions():
    svc = make_service()
    bg_task = MagicMock()
    svc._background.add(bg_task)
    grace_task = MagicMock()
    svc._empty_grace[10] = grace_task
    player = make_player(svc, current=make_track())
    player.stop_and_clear = AsyncMock()
    session = GuildMusicSession(player=player)
    session.cancel_tasks = MagicMock()
    svc.sessions[10] = session
    await svc.shutdown()
    bg_task.cancel.assert_called_once()
    assert grace_task.cancel.called  # снимается и в shutdown, и внутри cleanup(guild_id) — ок
    assert session.cancel_tasks.called  # то же: shutdown + cleanup
    player.stop_and_clear.assert_awaited_once()
    assert svc.sessions == {}


# --- enqueue_tracks -----------------------------------------------------------


async def test_enqueue_tracks_no_voice_channel_returns_false():
    svc = make_service()
    interaction = MagicMock()
    interaction.user = SimpleNamespace(voice=None)
    interaction.followup.send = AsyncMock()
    result = await svc.enqueue_tracks(interaction, [make_track()])
    assert result is False
    interaction.followup.send.assert_awaited_once()


async def test_enqueue_tracks_success_cancels_idle_and_enqueues():
    svc = make_service()
    player = MagicMock()
    player.enqueue = AsyncMock()
    session = GuildMusicSession(player=player)
    svc._get_or_create_session = AsyncMock(return_value=session)
    svc._ensure_message = AsyncMock()
    svc.cancel_idle = MagicMock()
    interaction = MagicMock()
    interaction.guild_id = 10
    interaction.channel = MagicMock()
    tracks = [make_track()]
    result = await svc.enqueue_tracks(interaction, tracks, to_front=True)
    assert result is True
    svc.cancel_idle.assert_called_once_with(10)
    svc._ensure_message.assert_awaited_once_with(10, interaction.channel)
    player.enqueue.assert_awaited_once_with(tracks, front=True)


# --- _get_or_create_session ----------------------------------------------------


async def test_get_or_create_session_user_not_in_voice():
    svc = make_service()
    interaction = MagicMock()
    interaction.user = SimpleNamespace(voice=None)
    interaction.followup.send = AsyncMock()
    result = await svc._get_or_create_session(interaction)
    assert result is None
    assert "не в голосовом" in interaction.followup.send.await_args.args[0]


async def test_get_or_create_session_connects_and_creates_session():
    svc = make_service()
    channel = MagicMock()
    channel.connect = AsyncMock(return_value=MagicMock())
    member = SimpleNamespace(voice=SimpleNamespace(channel=channel))
    guild = MagicMock()
    guild.id = 10
    guild.voice_client = None
    interaction = MagicMock()
    interaction.user = member
    interaction.guild = guild
    session = await svc._get_or_create_session(interaction)
    assert session is not None
    assert svc.sessions[10] is session
    channel.connect.assert_awaited_once_with(self_deaf=True)


async def test_get_or_create_session_connect_failure_reraises_when_no_vc():
    svc = make_service()
    channel = MagicMock()
    channel.connect = AsyncMock(side_effect=discord.ClientException("busy"))
    member = SimpleNamespace(voice=SimpleNamespace(channel=channel))
    guild = MagicMock()
    guild.id = 10
    guild.voice_client = None  # так и не появился
    interaction = MagicMock()
    interaction.user = member
    interaction.guild = guild
    with pytest.raises(discord.ClientException):
        await svc._get_or_create_session(interaction)


async def test_get_or_create_session_connect_races_existing_vc():
    svc = make_service()
    channel = MagicMock()
    existing_vc = MagicMock()
    existing_vc.channel = channel
    guild = SimpleNamespace(id=10, voice_client=None)

    async def connect_side_effect(*_a, **_kw):
        guild.voice_client = existing_vc  # кто-то успел подключиться конкурентно
        raise discord.ClientException("busy")

    channel.connect = AsyncMock(side_effect=connect_side_effect)
    member = SimpleNamespace(voice=SimpleNamespace(channel=channel))
    interaction = MagicMock()
    interaction.user = member
    interaction.guild = guild
    session = await svc._get_or_create_session(interaction)
    assert session is not None  # не перевыбросили — использовали появившийся vc


async def test_get_or_create_session_busy_elsewhere_blocks():
    svc = make_service()
    other_channel = MagicMock()
    target_channel = MagicMock()
    vc = MagicMock()
    vc.channel = other_channel
    member = SimpleNamespace(voice=SimpleNamespace(channel=target_channel))
    guild = MagicMock()
    guild.id = 10
    guild.voice_client = vc
    interaction = MagicMock()
    interaction.user = member
    interaction.guild = guild
    interaction.followup.send = AsyncMock()
    player = MagicMock()
    player.is_playing = True
    svc.sessions[10] = GuildMusicSession(player=player)
    result = await svc._get_or_create_session(interaction)
    assert result is None
    assert "другом голосовом" in interaction.followup.send.await_args.args[0]


async def test_get_or_create_session_moves_when_not_playing():
    svc = make_service()
    other_channel = MagicMock()
    target_channel = MagicMock()
    vc = MagicMock()
    vc.channel = other_channel
    vc.move_to = AsyncMock()
    member = SimpleNamespace(voice=SimpleNamespace(channel=target_channel))
    guild = MagicMock()
    guild.id = 10
    guild.voice_client = vc
    interaction = MagicMock()
    interaction.user = member
    interaction.guild = guild
    session = await svc._get_or_create_session(interaction)
    vc.move_to.assert_awaited_once_with(target_channel)
    assert session is not None


async def test_get_or_create_session_reuses_existing_session():
    svc = make_service()
    channel = MagicMock()
    vc = MagicMock()
    vc.channel = channel
    member = SimpleNamespace(voice=SimpleNamespace(channel=channel))
    guild = MagicMock()
    guild.id = 10
    guild.voice_client = vc
    interaction = MagicMock()
    interaction.user = member
    interaction.guild = guild
    existing = GuildMusicSession(player=MagicMock())
    svc.sessions[10] = existing
    session = await svc._get_or_create_session(interaction)
    assert session is existing


# --- _ensure_message ------------------------------------------------------------


async def test_ensure_message_no_session_noop():
    svc = make_service()
    await svc._ensure_message(10, MagicMock())  # нет сессии — не падает


async def test_ensure_message_same_channel_skips():
    svc = make_service()
    channel = MagicMock()
    channel.id = 1
    message = MagicMock()
    message.channel = SimpleNamespace(id=1)
    session = GuildMusicSession(player=make_player(svc), message=message)
    svc.sessions[10] = session
    await svc._ensure_message(10, channel)
    channel.send.assert_not_called()


async def test_ensure_message_different_channel_deletes_old_and_sends_new():
    svc = make_service()
    svc.view_factory = MagicMock(return_value=MagicMock())
    old_message = MagicMock()
    old_message.channel = SimpleNamespace(id=1)
    old_message.delete = AsyncMock()
    new_channel = MagicMock()
    new_channel.id = 2
    new_message = MagicMock()
    new_message.channel = SimpleNamespace(id=2)
    new_channel.send = AsyncMock(return_value=new_message)
    session = GuildMusicSession(player=make_player(svc), message=old_message)
    svc.sessions[10] = session
    try:
        await svc._ensure_message(10, new_channel)
        old_message.delete.assert_awaited_once()
        new_channel.send.assert_awaited_once()
        assert session.message is new_message
    finally:
        if session.updater_task is not None:
            session.updater_task.cancel()


async def test_ensure_message_delete_http_exception_ignored():
    svc = make_service()
    svc.view_factory = MagicMock(return_value=MagicMock())
    old_message = MagicMock()
    old_message.channel = SimpleNamespace(id=1)
    old_message.delete = AsyncMock(side_effect=http_error())
    new_channel = MagicMock()
    new_channel.id = 2
    new_message = MagicMock()
    new_message.channel = SimpleNamespace(id=2)
    new_channel.send = AsyncMock(return_value=new_message)
    session = GuildMusicSession(player=make_player(svc), message=old_message)
    svc.sessions[10] = session
    try:
        await svc._ensure_message(10, new_channel)  # не падает
        new_channel.send.assert_awaited_once()
    finally:
        if session.updater_task is not None:
            session.updater_task.cancel()


async def test_ensure_message_no_prior_message_sends_and_starts_updater():
    svc = make_service()
    svc.view_factory = MagicMock(return_value=MagicMock())
    channel = MagicMock()
    channel.id = 5
    new_message = MagicMock()
    new_message.channel = SimpleNamespace(id=5)
    channel.send = AsyncMock(return_value=new_message)
    session = GuildMusicSession(player=make_player(svc))
    svc.sessions[10] = session
    try:
        await svc._ensure_message(10, channel)
        assert session.message is new_message
        assert session.updater_task is not None
    finally:
        if session.updater_task is not None:
            session.updater_task.cancel()


# --- _player_snapshot / _write_snapshot -----------------------------------------


def test_player_snapshot_active_track():
    svc = make_service()
    player = make_player(svc, current=make_track(title="Тек"), queue=[make_track("q1")])
    state = svc._player_snapshot(player)
    assert state.is_active is True
    assert state.current.title == "Тек"
    assert len(state.queue) == 1


def test_player_snapshot_idle():
    svc = make_service()
    player = make_player(svc)
    state = svc._player_snapshot(player)
    assert state.is_active is False
    assert state.current is None


async def test_write_snapshot_noop_without_save_state():
    svc = make_service()
    await svc._write_snapshot(svc._player_snapshot(make_player(svc)))  # save_state=None


async def test_write_snapshot_calls_save_state():
    svc = make_service()
    svc._save_state = SimpleNamespace(execute=AsyncMock())
    state = svc._player_snapshot(make_player(svc))
    await svc._write_snapshot(state)
    svc._save_state.execute.assert_awaited_once_with(state)


async def test_write_snapshot_swallows_exception():
    svc = make_service()
    svc._save_state = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("db")))
    state = svc._player_snapshot(make_player(svc))
    await svc._write_snapshot(state)  # не падает


# --- _on_player_state -----------------------------------------------------------


async def test_on_player_state_refreshes_and_prefetches():
    svc = make_service()
    svc._refresh_message = AsyncMock()
    svc.refresh_presence = AsyncMock()
    svc._write_snapshot = AsyncMock()
    prefetch = MagicMock()
    svc.prefetch_lyrics = prefetch
    track = make_track()
    player = make_player(svc, current=track)
    svc.sessions[10] = GuildMusicSession(player=player)
    await svc._on_player_state(10)
    svc._refresh_message.assert_awaited_once_with(10)
    svc.refresh_presence.assert_awaited_once()
    svc._write_snapshot.assert_awaited_once()
    prefetch.assert_called_once_with(track)


async def test_on_player_state_no_player_skips_snapshot():
    svc = make_service()
    svc._refresh_message = AsyncMock()
    svc.refresh_presence = AsyncMock()
    svc._write_snapshot = AsyncMock()
    await svc._on_player_state(10)  # нет сессии
    svc._write_snapshot.assert_not_awaited()


async def test_on_player_state_idle_player_skips_prefetch():
    svc = make_service()
    svc._refresh_message = AsyncMock()
    svc.refresh_presence = AsyncMock()
    svc._write_snapshot = AsyncMock()
    prefetch = MagicMock()
    svc.prefetch_lyrics = prefetch
    svc.sessions[10] = GuildMusicSession(player=make_player(svc))  # current=None
    await svc._on_player_state(10)
    prefetch.assert_not_called()


# --- _on_track_failed -------------------------------------------------------------


async def test_on_track_failed_sends_message():
    svc = make_service()
    player = make_player(svc)
    player.text_channel_id = 55
    svc.sessions[10] = GuildMusicSession(player=player)
    channel = MagicMock()
    channel.send = AsyncMock()
    svc.bot.get_channel.return_value = channel
    await svc._on_track_failed(10, make_track(title="Мёртвый"), "404")
    channel.send.assert_awaited_once()


async def test_on_track_failed_cooldown_blocks_repeat():
    svc = make_service()
    player = make_player(svc)
    player.text_channel_id = 55
    svc.sessions[10] = GuildMusicSession(player=player)
    channel = MagicMock()
    channel.send = AsyncMock()
    svc.bot.get_channel.return_value = channel
    await svc._on_track_failed(10, make_track(), "404")
    await svc._on_track_failed(10, make_track(), "404")  # в кулдауне — не шлём второй раз
    channel.send.assert_awaited_once()


async def test_on_track_failed_no_player_returns():
    svc = make_service()
    await svc._on_track_failed(10, make_track(), "404")  # нет сессии — не падает
    svc.bot.get_channel.assert_not_called()


async def test_on_track_failed_no_channel_id_skips_send():
    svc = make_service()
    player = make_player(svc)  # text_channel_id по умолчанию 0
    svc.sessions[10] = GuildMusicSession(player=player)
    await svc._on_track_failed(10, make_track(), "404")
    svc.bot.get_channel.assert_not_called()


async def test_on_track_failed_channel_missing_returns():
    svc = make_service()
    player = make_player(svc)
    player.text_channel_id = 55
    svc.sessions[10] = GuildMusicSession(player=player)
    svc.bot.get_channel.return_value = None
    await svc._on_track_failed(10, make_track(), "404")  # не падает


async def test_on_track_failed_send_http_exception_ignored():
    svc = make_service()
    player = make_player(svc)
    player.text_channel_id = 55
    svc.sessions[10] = GuildMusicSession(player=player)
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=http_error())
    svc.bot.get_channel.return_value = channel
    await svc._on_track_failed(10, make_track(), "404")  # не падает


# --- _refresh_message --------------------------------------------------------------


async def test_refresh_message_no_session_noop():
    svc = make_service()
    await svc._refresh_message(10)


async def test_refresh_message_no_message_noop():
    svc = make_service()
    svc.sessions[10] = GuildMusicSession(player=make_player(svc))
    await svc._refresh_message(10)


async def test_refresh_message_edits_existing():
    svc = make_service()
    message = MagicMock()
    message.edit = AsyncMock()
    svc.sessions[10] = GuildMusicSession(player=make_player(svc), message=message)
    await svc._refresh_message(10)
    message.edit.assert_awaited_once()


async def test_refresh_message_not_found_recreates():
    svc = make_service()
    svc.view_factory = MagicMock(return_value=MagicMock())
    message = MagicMock()
    message.channel = MagicMock()
    message.channel.id = 1
    message.edit = AsyncMock(side_effect=not_found())
    new_message = MagicMock()
    new_message.channel = SimpleNamespace(id=1)
    message.channel.send = AsyncMock(return_value=new_message)
    session = GuildMusicSession(player=make_player(svc), message=message)
    svc.sessions[10] = session
    try:
        await svc._refresh_message(10)
        assert session.message is new_message
    finally:
        if session.updater_task is not None:
            session.updater_task.cancel()


async def test_refresh_message_http_exception_logged():
    svc = make_service()
    message = MagicMock()
    message.edit = AsyncMock(side_effect=http_error())
    svc.sessions[10] = GuildMusicSession(player=make_player(svc), message=message)
    await svc._refresh_message(10)  # не падает


# --- _start_updater / _updater_loop -------------------------------------------------


def test_start_updater_no_session_noop():
    svc = make_service()
    svc._start_updater(10)  # не падает


def test_start_updater_skips_if_already_running():
    svc = make_service()
    session = GuildMusicSession(player=make_player(svc))
    fake_task = MagicMock()
    fake_task.done.return_value = False
    session.updater_task = fake_task
    svc.sessions[10] = session
    svc._start_updater(10)
    assert session.updater_task is fake_task  # не пересоздали


async def _drive_updater_loop(monkeypatch, svc, guild_id, iterations):
    calls = 0

    async def fake_sleep(_):
        nonlocal calls
        calls += 1
        if calls > iterations:
            raise asyncio.CancelledError()

    monkeypatch.setattr(service_module.asyncio, "sleep", fake_sleep)
    with contextlib.suppress(asyncio.CancelledError):
        await svc._updater_loop(guild_id)


async def test_updater_loop_refreshes_while_playing(monkeypatch):
    svc = make_service()
    player = make_player(svc, current=make_track())
    svc.sessions[10] = GuildMusicSession(player=player)
    svc._refresh_message = AsyncMock()
    await _drive_updater_loop(monkeypatch, svc, 10, iterations=1)
    svc._refresh_message.assert_awaited_once_with(10)


async def test_updater_loop_skips_refresh_when_paused(monkeypatch):
    svc = make_service()
    player = make_player(svc, current=make_track(), paused=True)
    svc.sessions[10] = GuildMusicSession(player=player)
    svc._refresh_message = AsyncMock()
    await _drive_updater_loop(monkeypatch, svc, 10, iterations=1)
    svc._refresh_message.assert_not_awaited()


async def test_updater_loop_stops_when_player_gone(monkeypatch):
    svc = make_service()
    svc._refresh_message = AsyncMock()
    await _drive_updater_loop(monkeypatch, svc, 10, iterations=5)  # цикл сам вернётся
    svc._refresh_message.assert_not_awaited()


# --- cancel_idle: с активным напоминанием ---------------------------------------


def test_cancel_idle_cancels_task_and_deletes_prompt():
    svc = make_service()
    idle_task = MagicMock()
    prompt = MagicMock()
    session = GuildMusicSession(player=make_player(svc), idle_task=idle_task, idle_prompt=prompt)
    svc.sessions[10] = session
    # side_effect закрывает переданную корутину — иначе RuntimeWarning "never awaited"
    svc.spawn = MagicMock(side_effect=lambda coro: coro.close())
    svc.cancel_idle(10)
    idle_task.cancel.assert_called_once()
    assert session.idle_task is None
    assert session.idle_prompt is None
    svc.spawn.assert_called_once()


# --- _on_idle ------------------------------------------------------------------------


async def test_on_idle_radio_fills_and_returns_early():
    svc = make_service()
    svc.refresh_presence = AsyncMock()
    radio = MagicMock()
    radio.is_enabled.return_value = True
    radio.recently_filled.return_value = False
    radio.fill = AsyncMock(return_value=True)
    svc.radio = radio
    await svc._on_idle(10)
    radio.fill.assert_awaited_once_with(10)
    svc.refresh_presence.assert_not_awaited()  # вышли раньше


async def test_on_idle_radio_recently_filled_skips_fill():
    svc = make_service()
    svc.refresh_presence = AsyncMock()
    radio = MagicMock()
    radio.is_enabled.return_value = True
    radio.recently_filled.return_value = True
    radio.fill = AsyncMock()
    svc.radio = radio
    await svc._on_idle(10)
    radio.fill.assert_not_awaited()
    svc.refresh_presence.assert_awaited_once()


async def test_on_idle_radio_fill_exception_falls_through():
    svc = make_service()
    svc.refresh_presence = AsyncMock()
    radio = MagicMock()
    radio.is_enabled.return_value = True
    radio.recently_filled.return_value = False
    radio.fill = AsyncMock(side_effect=RuntimeError("boom"))
    svc.radio = radio
    await svc._on_idle(10)  # не падает
    svc.refresh_presence.assert_awaited_once()


async def test_on_idle_radio_fill_false_continues_normal_flow():
    svc = make_service()
    svc.refresh_presence = AsyncMock()
    radio = MagicMock()
    radio.is_enabled.return_value = True
    radio.recently_filled.return_value = False
    radio.fill = AsyncMock(return_value=False)
    svc.radio = radio
    await svc._on_idle(10)
    svc.refresh_presence.assert_awaited_once()


async def test_on_idle_no_session_returns_after_presence():
    svc = make_service()
    svc.refresh_presence = AsyncMock()
    await svc._on_idle(10)  # нет сессии
    svc.refresh_presence.assert_awaited_once()


async def test_on_idle_deletes_message_and_starts_countdown():
    svc = make_service()
    svc.refresh_presence = AsyncMock()
    message = MagicMock()
    message.delete = AsyncMock()
    session = GuildMusicSession(player=make_player(svc), message=message)
    svc.sessions[10] = session
    try:
        await svc._on_idle(10)
        assert session.message is None
        assert session.idle_task is not None
    finally:
        if session.idle_task is not None:
            session.idle_task.cancel()


# --- _idle_countdown -----------------------------------------------------------------


async def test_idle_countdown_returns_if_still_playing(monkeypatch):
    async def fake_sleep(_):
        pass

    monkeypatch.setattr(service_module.asyncio, "sleep", fake_sleep)
    svc = make_service()
    player = make_player(svc, current=make_track())
    svc.sessions[10] = GuildMusicSession(player=player)
    svc.cleanup = AsyncMock()
    await svc._idle_countdown(10)
    svc.cleanup.assert_not_awaited()


async def test_idle_countdown_sends_prompt_and_cleans_up(monkeypatch):
    async def fake_sleep(_):
        pass

    monkeypatch.setattr(service_module.asyncio, "sleep", fake_sleep)
    svc = make_service()
    player = make_player(svc)  # is_playing=False
    player.text_channel_id = 55
    svc.sessions[10] = GuildMusicSession(player=player)
    channel = MagicMock()
    channel.send = AsyncMock(return_value=MagicMock())
    svc.bot.get_channel.return_value = channel
    svc.cleanup = AsyncMock()
    await svc._idle_countdown(10)
    channel.send.assert_awaited_once()
    svc.cleanup.assert_awaited_once()


async def test_idle_countdown_no_channel_id_skips_prompt(monkeypatch):
    async def fake_sleep(_):
        pass

    monkeypatch.setattr(service_module.asyncio, "sleep", fake_sleep)
    svc = make_service()
    player = make_player(svc)
    player.text_channel_id = 0
    svc.sessions[10] = GuildMusicSession(player=player)
    svc.cleanup = AsyncMock()
    await svc._idle_countdown(10)
    svc.bot.get_channel.assert_not_called()
    svc.cleanup.assert_awaited_once()


async def test_idle_countdown_prompt_http_exception_ignored(monkeypatch):
    async def fake_sleep(_):
        pass

    monkeypatch.setattr(service_module.asyncio, "sleep", fake_sleep)
    svc = make_service()
    player = make_player(svc)
    player.text_channel_id = 55
    svc.sessions[10] = GuildMusicSession(player=player)
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=http_error())
    svc.bot.get_channel.return_value = channel
    svc.cleanup = AsyncMock()
    await svc._idle_countdown(10)  # не падает
    svc.cleanup.assert_awaited_once()


async def test_idle_countdown_became_playing_during_warn_skips_cleanup(monkeypatch):
    svc = make_service()
    player = make_player(svc)
    player.text_channel_id = 0
    svc.sessions[10] = GuildMusicSession(player=player)
    calls = {"n": 0}

    async def fake_sleep(_):
        calls["n"] += 1
        if calls["n"] == 2:
            player.current = make_track()  # заиграла во время предупреждения

    monkeypatch.setattr(service_module.asyncio, "sleep", fake_sleep)
    svc.cleanup = AsyncMock()
    await svc._idle_countdown(10)
    svc.cleanup.assert_not_awaited()


async def test_idle_countdown_zero_warn_skips_prompt_entirely(monkeypatch):
    async def fake_sleep(_):
        pass

    monkeypatch.setattr(service_module.asyncio, "sleep", fake_sleep)
    settings = make_settings()
    settings.music_idle_warn_seconds = 0
    svc = make_service(settings)
    player = make_player(svc)
    svc.sessions[10] = GuildMusicSession(player=player)
    svc.cleanup = AsyncMock()
    await svc._idle_countdown(10)
    svc.bot.get_channel.assert_not_called()
    svc.cleanup.assert_awaited_once()


# --- handle_voice_state: недостающие ветки --------------------------------------


async def test_handle_voice_state_no_session_returns():
    svc = make_service()
    member = MagicMock()
    member.guild = SimpleNamespace(id=999, voice_client=None)
    state = SimpleNamespace(channel=None)
    await svc.handle_voice_state(member, state, state)  # нет сессии для этой гильдии


async def test_handle_voice_state_bot_moved_not_disconnected_falls_through():
    svc = make_service()
    svc.cleanup = AsyncMock()
    svc.sessions[10] = GuildMusicSession(player=make_player(svc))
    member = MagicMock()
    member.id = 999  # сам бот
    member.guild = SimpleNamespace(id=10, voice_client=None)
    before = SimpleNamespace(channel=MagicMock())
    after = SimpleNamespace(channel=MagicMock())  # переместили, не отключили
    await svc.handle_voice_state(member, before, after)
    svc.cleanup.assert_not_awaited()


async def test_handle_voice_state_other_bot_with_session_returns_early():
    svc = make_service()
    svc.cleanup = AsyncMock()
    svc.sessions[10] = GuildMusicSession(player=make_player(svc))
    member = MagicMock()
    member.id = 5  # не бот-владелец
    member.bot = True
    member.guild = SimpleNamespace(id=10, voice_client=MagicMock())
    state = SimpleNamespace(channel=None)
    await svc.handle_voice_state(member, state, state)
    svc.cleanup.assert_not_awaited()  # чужой бот — выходим до проверки войса


async def test_handle_voice_state_no_voice_client_returns():
    svc = make_service()
    svc.sessions[10] = GuildMusicSession(player=make_player(svc))
    member = MagicMock()
    member.id = 1
    member.bot = False
    member.guild = SimpleNamespace(id=10, voice_client=None)
    state = SimpleNamespace(channel=None)
    await svc.handle_voice_state(member, state, state)  # не падает


async def test_handle_voice_state_vc_channel_none_returns():
    svc = make_service()
    svc.sessions[10] = GuildMusicSession(player=make_player(svc))
    vc = SimpleNamespace(channel=None)
    member = MagicMock()
    member.id = 1
    member.bot = False
    member.guild = SimpleNamespace(id=10, voice_client=vc)
    state = SimpleNamespace(channel=None)
    await svc.handle_voice_state(member, state, state)


# --- _start_empty_grace --------------------------------------------------------------


def test_start_empty_grace_does_not_duplicate():
    svc = make_service()
    task = MagicMock()
    svc._empty_grace[10] = task
    svc._start_empty_grace(10)
    assert svc._empty_grace[10] is task  # не пересоздали


# --- _empty_countdown: недостающие ветки ----------------------------------------


async def test_empty_countdown_cancelled_returns_quietly():
    svc = make_service()
    orig = service_module._EMPTY_GRACE_SECONDS
    service_module._EMPTY_GRACE_SECONDS = 5
    try:
        task = asyncio.create_task(svc._empty_countdown(10))
        await asyncio.sleep(0)
        task.cancel()
        await task  # исключение поймано внутри — наружу не летит
    finally:
        service_module._EMPTY_GRACE_SECONDS = orig


async def test_empty_countdown_guild_missing_returns():
    svc = make_service()
    svc.bot.get_guild = MagicMock(return_value=None)
    orig = service_module._EMPTY_GRACE_SECONDS
    service_module._EMPTY_GRACE_SECONDS = 0
    try:
        await svc._empty_countdown(10)  # не падает
    finally:
        service_module._EMPTY_GRACE_SECONDS = orig


async def test_empty_countdown_vc_channel_missing_returns():
    svc = make_service()
    svc.bot.get_guild = MagicMock(
        return_value=SimpleNamespace(voice_client=SimpleNamespace(channel=None))
    )
    orig = service_module._EMPTY_GRACE_SECONDS
    service_module._EMPTY_GRACE_SECONDS = 0
    try:
        await svc._empty_countdown(10)
    finally:
        service_module._EMPTY_GRACE_SECONDS = orig


# --- cleanup: недостающие ветки --------------------------------------------------


async def test_cleanup_spawns_deletion_of_leftover_messages():
    svc = make_service()
    player = make_player(svc)
    player.stop_and_clear = AsyncMock()
    idle_prompt = MagicMock()
    karaoke_message = MagicMock()
    session = GuildMusicSession(
        player=player, idle_prompt=idle_prompt, karaoke_message=karaoke_message
    )
    svc.sessions[10] = session
    svc.spawn = MagicMock(side_effect=lambda coro: coro.close())  # закрыть корутины удаления
    await svc.cleanup(10, "reason")
    assert svc.spawn.call_count == 2


async def test_cleanup_swallows_stop_and_clear_exception():
    svc = make_service()
    player = make_player(svc)
    player.stop_and_clear = AsyncMock(side_effect=RuntimeError("boom"))
    svc.sessions[10] = GuildMusicSession(player=player)
    await svc.cleanup(10, "reason")  # не падает


async def test_cleanup_message_edit_http_exception_ignored():
    svc = make_service()
    player = make_player(svc)
    player.stop_and_clear = AsyncMock()
    message = MagicMock()
    message.edit = AsyncMock(side_effect=http_error())
    svc.sessions[10] = GuildMusicSession(player=player, message=message)
    await svc.cleanup(10, "reason")  # не падает


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
    monkeypatch.setattr(
        "src.infrastructure.discord.cogs.music.radio.time.monotonic", lambda: clock["t"]
    )
    radio = make_radio()
    assert radio.recently_filled(10) is False  # ни разу не заполняли
    radio._last_fill[10] = 1000.0
    clock["t"] = 1010.0
    assert radio.recently_filled(10) is True  # 10 c < 30 c
    clock["t"] = 1040.0
    assert radio.recently_filled(10) is False  # 40 c > 30 c


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
    player.history = []  # нечего играло — нет и сида для микса
    session = GuildMusicSession(player=player)
    channel = SimpleNamespace(members=[])  # никого в войсе
    guild = SimpleNamespace(voice_client=SimpleNamespace(channel=channel))
    container = MagicMock()
    container.list_playlists.execute = AsyncMock(return_value=[])  # и плейлистов нет
    radio = make_radio(session=session, guild=guild, container=container)
    assert await radio.fill(10) is False
    player.enqueue.assert_not_awaited()


async def test_radio_fill_uses_youtube_mix_when_no_other_source():
    player = MagicMock()
    player.enqueue = AsyncMock()
    player.history = [make_track("seed")]  # последний игравший — основа микса
    session = GuildMusicSession(player=player)
    channel = SimpleNamespace(members=[])  # ни лайков
    guild = SimpleNamespace(voice_client=SimpleNamespace(channel=channel))
    container = MagicMock()
    container.list_playlists.execute = AsyncMock(return_value=[])  # ни плейлистов
    container.audio_source.resolve = AsyncMock(
        return_value=[make_track("seed"), make_track("sim1"), make_track("sim2")]
    )
    radio = make_radio(session=session, guild=guild, container=container)
    assert await radio.fill(10) is True
    url = container.audio_source.resolve.await_args.args[0]
    assert "list=RDseed" in url  # резолвил Mix именно сида
    enqueued = player.enqueue.await_args.args[0]
    assert enqueued and all(t.video_id != "seed" for t in enqueued)  # сид не в очередь


async def test_radio_fill_mix_failure_returns_false():
    player = MagicMock()
    player.enqueue = AsyncMock()
    player.history = [make_track("seed")]
    session = GuildMusicSession(player=player)
    guild = SimpleNamespace(voice_client=SimpleNamespace(channel=SimpleNamespace(members=[])))
    container = MagicMock()
    container.list_playlists.execute = AsyncMock(return_value=[])
    container.audio_source.resolve = AsyncMock(side_effect=RuntimeError("сеть"))
    radio = make_radio(session=session, guild=guild, container=container)
    assert await radio.fill(10) is False  # микс упал — не падаем, просто нечем добрать
    player.enqueue.assert_not_awaited()
