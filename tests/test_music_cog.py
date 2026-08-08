"""MusicCog: биндинг слеш-команд к сервисам. service/radio/lyrics подменяются
моками — проверяем разбор аргументов и ответы."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.domain.music.entities import LikedTrack, Track
from src.domain.music.exceptions import TrackResolveError
from src.infrastructure.discord.cogs.music.cog import MusicCog
from tests.cog_fakes import make_member

NOW_URL = "https://youtube.com/watch?v=abc"


def make_track(vid="a", title="Песня", duration=200):
    return Track(
        video_id=vid, title=title, url=f"https://youtu.be/{vid}", duration=duration, requested_by=1
    )


def make_liked(vid, title=None):
    import datetime

    return LikedTrack(
        user_id=1,
        video_id=vid,
        title=title or vid,
        uploader="Artist",
        duration=180,
        liked_at=datetime.datetime.now(),
    )


def make_settings():
    return SimpleNamespace(
        music_playlist_limit=50,
        music_search_limit=5,
        music_playlist_max_per_guild=25,
        music_liked_max_per_user=300,
        music_default_volume=0.5,
        music_prefetch_tracks=0,
        ffmpeg_path="ffmpeg",
        music_progress_interval=5,
        music_idle_timeout=300,
        music_idle_warn_seconds=120,
        music_lyrics_offset=1.0,
        presence_rotate_minutes=30,
        spotify_client_id="",
        spotify_client_secret="",
    )


def make_container():
    c = SimpleNamespace()
    c.settings = make_settings()
    c.audio_source = MagicMock()
    c.audio_source.search = AsyncMock(return_value=[])
    c.audio_source.resolve = AsyncMock(return_value=[])
    c.event_bus = MagicMock()
    c.save_playlist = SimpleNamespace(execute=AsyncMock(return_value=""))
    c.load_playlist = SimpleNamespace(execute=AsyncMock(return_value=None))
    c.list_playlists = SimpleNamespace(execute=AsyncMock(return_value=[]))
    c.delete_playlist = SimpleNamespace(execute=AsyncMock(return_value="ok"))
    c.toggle_like = SimpleNamespace(execute=AsyncMock(return_value="liked"))
    c.list_liked = SimpleNamespace(execute=AsyncMock(return_value=[]))
    c.remove_liked = SimpleNamespace(execute=AsyncMock(return_value=True))
    c.resolve_liked = SimpleNamespace(execute=AsyncMock(return_value=None))
    return c


def make_cog(container=None):
    bot = MagicMock()
    bot.user = SimpleNamespace(id=999)
    cog = MusicCog(bot, container or make_container())
    # подменяем сервисы моками для изоляции команд
    cog.service = MagicMock()
    cog.service.get_player = MagicMock(return_value=None)
    cog.service.get_session = MagicMock(return_value=None)
    cog.service.cleanup = AsyncMock()
    cog.service.enqueue_tracks = AsyncMock(return_value=True)
    cog.service.cancel_idle = MagicMock()
    cog.service.build_embed = MagicMock(return_value=MagicMock())
    cog.radio = MagicMock()
    cog.lyrics = MagicMock()
    return cog


def make_interaction(user_id=1, in_voice=True):
    interaction = MagicMock()
    interaction.guild_id = 10
    interaction.user = SimpleNamespace(
        id=user_id,
        display_name="Гость",
        mention=f"<@{user_id}>",
        voice=SimpleNamespace(channel=MagicMock()) if in_voice else None,
        guild_permissions=SimpleNamespace(administrator=False),
    )
    interaction.channel = MagicMock()
    interaction.channel.send = AsyncMock()
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def make_player(current=None, queue=(), paused=False):
    player = MagicMock()
    player.current = current
    player.queue = list(queue)
    player.is_paused = paused
    player.skip = AsyncMock()
    player.shuffle = AsyncMock()
    player.toggle_pause = AsyncMock()
    player.set_volume = AsyncMock()
    player.all_tracks = MagicMock(return_value=list(queue))
    return player


# --- queue / skip / shuffle -------------------------------------------------


async def test_queue_empty():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).queue.callback(cog, interaction)
    assert "пуста" in interaction.response.send_message.await_args.args[0]


async def test_queue_lists_current_and_upcoming():
    cog = make_cog()
    cog.service.get_player.return_value = make_player(
        current=make_track("a", "Текущий"), queue=[make_track("b", "Следующий")]
    )
    interaction = make_interaction()
    await type(cog).queue.callback(cog, interaction)
    assert "embed" in interaction.response.send_message.await_args.kwargs


async def test_skip_nothing_playing():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).skip.callback(cog, interaction)
    assert "ничего не играет" in interaction.response.send_message.await_args.args[0]


async def test_skip_ok():
    cog = make_cog()
    player = make_player(current=make_track())
    cog.service.get_player.return_value = player
    interaction = make_interaction()
    await type(cog).skip.callback(cog, interaction)
    player.skip.assert_awaited_once()


async def test_shuffle_nothing():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).shuffle.callback(cog, interaction)
    assert "нечего" in interaction.response.send_message.await_args.args[0]


async def test_shuffle_ok():
    cog = make_cog()
    player = make_player(queue=[make_track("a"), make_track("b")])
    cog.service.get_player.return_value = player
    interaction = make_interaction()
    await type(cog).shuffle.callback(cog, interaction)
    player.shuffle.assert_awaited_once()


# --- stop / pause / resume / volume / nowplaying / leave --------------------


async def test_stop_when_idle():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).stop.callback(cog, interaction)
    assert "ничего не играю" in interaction.response.send_message.await_args.args[0]


async def test_stop_cleans_up():
    cog = make_cog()
    cog.service.get_session.return_value = MagicMock()
    interaction = make_interaction()
    await type(cog).stop.callback(cog, interaction)
    cog.service.cleanup.assert_awaited_once()


async def test_pause_and_resume():
    cog = make_cog()
    player = make_player(current=make_track(), paused=False)
    cog.service.get_player.return_value = player
    interaction = make_interaction()
    await type(cog).pause.callback(cog, interaction)
    player.toggle_pause.assert_awaited_once()

    player.is_paused = True
    interaction2 = make_interaction()
    await type(cog).resume.callback(cog, interaction2)
    assert player.toggle_pause.await_count == 2


async def test_volume_sets():
    cog = make_cog()
    player = make_player(current=make_track())
    cog.service.get_player.return_value = player
    interaction = make_interaction()
    await type(cog).volume.callback(cog, interaction, 150)
    player.set_volume.assert_awaited_once_with(1.5)


async def test_nowplaying_idle():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).nowplaying.callback(cog, interaction)
    assert "Тишина" in interaction.response.send_message.await_args.args[0]


async def test_nowplaying_shows_embed():
    cog = make_cog()
    cog.service.get_player.return_value = make_player(current=make_track())
    interaction = make_interaction()
    await type(cog).nowplaying.callback(cog, interaction)
    assert "embed" in interaction.response.send_message.await_args.kwargs


async def test_leave_when_absent():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).leave.callback(cog, interaction)
    assert "Меня и так нет" in interaction.response.send_message.await_args.args[0]


# --- _play_request ----------------------------------------------------------


async def test_play_requires_voice():
    cog = make_cog()
    interaction = make_interaction(in_voice=False)
    await type(cog).play.callback(cog, interaction, "queen")
    assert "голосовой канал" in interaction.response.send_message.await_args.args[0]


async def test_play_search_offers_view():
    container = make_container()
    container.audio_source.search.return_value = [make_track("a"), make_track("b")]
    cog = make_cog(container)
    cog.service.enqueue_tracks = AsyncMock(return_value=True)
    interaction = make_interaction()
    await type(cog).play.callback(cog, interaction, "queen")
    assert "view" in interaction.followup.send.await_args.kwargs


async def test_play_url_resolves_and_enqueues():
    container = make_container()
    container.audio_source.resolve.return_value = [make_track("a", "Трек")]
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).play.callback(cog, interaction, NOW_URL)
    cog.service.enqueue_tracks.assert_awaited_once()


async def test_play_url_resolve_error():
    container = make_container()
    container.audio_source.resolve.side_effect = TrackResolveError("dead")
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).play.callback(cog, interaction, NOW_URL)
    assert "Не смогла открыть" in interaction.followup.send.await_args.args[0]


async def test_play_spotify_playlist_needs_api_without_keys():
    """Без SPOTIFY_CLIENT_ID/SECRET плейлист недоступен — честно об этом говорим."""
    cog = make_cog()  # ключей в make_settings нет
    interaction = make_interaction()
    await type(cog).play.callback(cog, interaction, "https://open.spotify.com/album/x")
    assert "нужен их API" in interaction.followup.send.await_args.args[0]


async def test_play_spotify_playlist_enqueues_youtube_matches():
    container = make_container()
    container.settings.spotify_client_id = "id"
    container.settings.spotify_client_secret = "sec"
    container.audio_source.search.side_effect = lambda q, requested_by, limit: [make_track(q[:1])]
    cog = make_cog(container)
    cog.spotify.track_queries_for = AsyncMock(return_value=["Artist A", "Band B"])
    interaction = make_interaction()

    await type(cog).play.callback(cog, interaction, "https://open.spotify.com/playlist/abc")

    cog.service.enqueue_tracks.assert_awaited_once()
    tracks = cog.service.enqueue_tracks.await_args.args[1]
    assert len(tracks) == 2
    assert "**2** из 2" in interaction.followup.send.await_args.args[0]


async def test_play_spotify_playlist_failed_when_api_empty():
    container = make_container()
    container.settings.spotify_client_id = "id"
    container.settings.spotify_client_secret = "sec"
    cog = make_cog(container)
    cog.spotify.track_queries_for = AsyncMock(return_value=[])  # битая ссылка / сбой API
    interaction = make_interaction()
    await type(cog).play.callback(cog, interaction, "https://open.spotify.com/playlist/abc")
    assert "плейлист Spotify" in interaction.followup.send.await_args.args[0]


# --- taste ------------------------------------------------------------------


async def test_taste_with_bot():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).taste.callback(cog, interaction, make_member(bot=True))
    assert "ботов нет вкуса" in interaction.response.send_message.await_args.args[0]


async def test_taste_self():
    cog = make_cog()
    interaction = make_interaction(user_id=1)
    await type(cog).taste.callback(cog, interaction, make_member(uid=1))
    assert "нарцисс" in interaction.response.send_message.await_args.args[0]


async def test_taste_no_likes():
    container = make_container()
    container.list_liked.execute.return_value = []
    cog = make_cog(container)
    interaction = make_interaction(user_id=1)
    await type(cog).taste.callback(cog, interaction, make_member(uid=2))
    assert "нет лайков" in interaction.followup.send.await_args.args[0]


async def test_taste_computes_percentage():
    container = make_container()
    mine = [make_liked("a"), make_liked("b")]
    theirs = [make_liked("a"), make_liked("c")]
    container.list_liked.execute = AsyncMock(side_effect=[mine, theirs])
    cog = make_cog(container)
    interaction = make_interaction(user_id=1)
    await type(cog).taste.callback(cog, interaction, make_member(uid=2, name="Друг"))
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert "Совместимость" in embed.title


# --- radio ------------------------------------------------------------------


async def test_radio_toggle_off():
    cog = make_cog()
    cog.radio.toggle.return_value = False
    interaction = make_interaction()
    await type(cog).radio_command.callback(cog, interaction)
    assert "выключено" in interaction.response.send_message.await_args.args[0]


async def test_radio_toggle_on():
    cog = make_cog()
    cog.radio.toggle.return_value = True
    cog.service.get_player.return_value = None
    interaction = make_interaction()
    await type(cog).radio_command.callback(cog, interaction)
    assert "включено" in interaction.response.send_message.await_args.args[0]


# --- toggle_like_current ----------------------------------------------------


async def test_toggle_like_nothing_playing():
    cog = make_cog()
    interaction = make_interaction()
    await cog.toggle_like_current(interaction)
    assert "ничего не играет" in interaction.response.send_message.await_args.args[0]


async def test_toggle_like_liked():
    container = make_container()
    container.toggle_like.execute.return_value = "liked"
    cog = make_cog(container)
    cog.service.get_player.return_value = make_player(current=make_track(title="Хит"))
    interaction = make_interaction()
    await cog.toggle_like_current(interaction)
    assert "В твоих лайках" in interaction.followup.send.await_args.args[0]


# --- playlists --------------------------------------------------------------


async def test_playlist_save_empty_queue():
    container = make_container()
    container.save_playlist.execute.return_value = "empty"
    cog = make_cog(container)
    cog.service.get_player.return_value = None
    interaction = make_interaction()
    await type(cog).playlist_save.callback(cog, interaction, "Микс")
    assert "нечего" in interaction.followup.send.await_args.args[0]


async def test_playlist_play_not_found():
    container = make_container()
    container.load_playlist.execute.return_value = None
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).playlist_play.callback(cog, interaction, "нет")
    assert "Такого плейлиста нет" in interaction.followup.send.await_args.args[0]


async def test_playlist_list_empty():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).playlist_list.callback(cog, interaction)
    assert "пока нет" in interaction.followup.send.await_args.args[0]


async def test_playlist_delete_ok():
    container = make_container()
    container.delete_playlist.execute.return_value = "ok"
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).playlist_delete.callback(cog, interaction, "Микс")
    assert "удалён" in interaction.followup.send.await_args.args[0]


# --- liked ------------------------------------------------------------------


async def test_liked_list_empty():
    cog = make_cog()
    interaction = make_interaction(user_id=1)
    target = make_member(uid=1)
    await type(cog).liked_list.callback(cog, interaction, target)
    assert "Пусто" in interaction.followup.send.await_args.args[0]


async def test_liked_remove():
    container = make_container()
    container.remove_liked.execute.return_value = True
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).liked_remove.callback(cog, interaction, "vid")
    assert "Убрала" in interaction.followup.send.await_args.args[0]


async def test_liked_play_resolve_fail():
    container = make_container()
    container.resolve_liked.execute.return_value = None
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).liked_play.callback(cog, interaction, "vid")
    assert "Не смогла оживить" in interaction.followup.send.await_args.args[0]


async def test_liked_all_empty():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).liked_all.callback(cog, interaction)
    assert "Лайков пока нет" in interaction.followup.send.await_args.args[0]


# --- history / save-queue ---------------------------------------------------


async def test_history_empty():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).history.callback(cog, interaction)
    assert "История пуста" in interaction.response.send_message.await_args.args[0]


async def test_history_lists_recent():
    cog = make_cog()
    player = make_player()
    player.history = [make_track("a", "Старый"), make_track("b", "Свежий")]
    cog.service.get_player.return_value = player
    interaction = make_interaction()
    await type(cog).history.callback(cog, interaction)
    kwargs = interaction.response.send_message.await_args.kwargs
    assert "embed" in kwargs and "view" in kwargs
    # свежие первыми
    assert kwargs["embed"].description.index("Свежий") < kwargs["embed"].description.index("Старый")


async def test_save_queue_empty():
    cog = make_cog()
    player = make_player()
    player.all_tracks = MagicMock(return_value=[])
    cog.service.get_player.return_value = player
    interaction = make_interaction()
    await cog.save_queue_current(interaction)
    assert "нечего сохранять" in interaction.response.send_message.await_args.args[0]


async def test_save_queue_opens_modal():
    cog = make_cog()
    player = make_player()
    player.all_tracks = MagicMock(return_value=[make_track("a")])
    cog.service.get_player.return_value = player
    interaction = make_interaction()
    interaction.response.send_modal = AsyncMock()
    await cog.save_queue_current(interaction)
    interaction.response.send_modal.assert_awaited_once()


async def test_lyricsfile_nothing_playing():
    cog = make_cog()
    interaction = make_interaction()
    file = MagicMock()
    await type(cog).lyrics_file.callback(cog, interaction, file)
    assert "ничего не играет" in interaction.response.send_message.await_args.args[0]


async def test_lyricsfile_wrong_extension():
    cog = make_cog()
    cog.service.get_player.return_value = make_player(current=make_track())
    interaction = make_interaction()
    file = MagicMock()
    file.filename = "text.txt"
    file.size = 100
    await type(cog).lyrics_file.callback(cog, interaction, file)
    assert ".lrc" in interaction.response.send_message.await_args.args[0]


async def test_lyricsfile_accepts_valid():
    cog = make_cog()
    cog.service.get_player.return_value = make_player(current=make_track("vid"))
    cog.lyrics = MagicMock()
    cog.lyrics.set_synced_lrc = MagicMock(return_value=True)
    interaction = make_interaction()
    file = MagicMock()
    file.filename = "song.lrc"
    file.size = 500
    file.read = AsyncMock(return_value=b"[00:01.00] line")
    await type(cog).lyrics_file.callback(cog, interaction, file)
    cog.lyrics.set_synced_lrc.assert_called_once()
    assert "Приняла" in interaction.followup.send.await_args.args[0]


async def test_lyricsfile_rejects_bad_content():
    cog = make_cog()
    cog.service.get_player.return_value = make_player(current=make_track("vid"))
    cog.lyrics = MagicMock()
    cog.lyrics.set_synced_lrc = MagicMock(return_value=False)
    interaction = make_interaction()
    file = MagicMock()
    file.filename = "song.lrc"
    file.size = 500
    file.read = AsyncMock(return_value=b"no timecodes here")
    await type(cog).lyrics_file.callback(cog, interaction, file)
    assert "не похоже на .lrc" in interaction.followup.send.await_args.args[0]


async def test_do_save_queue_persists():
    container = make_container()
    container.save_playlist.execute.return_value = ""
    cog = make_cog(container)
    player = make_player()
    player.all_tracks = MagicMock(return_value=[make_track("a"), make_track("b")])
    cog.service.get_player.return_value = player
    interaction = make_interaction()
    await cog._do_save_queue(interaction, "Вечерний чилл")
    container.save_playlist.execute.assert_awaited_once()
    assert "Сохранила" in interaction.followup.send.await_args.args[0]
