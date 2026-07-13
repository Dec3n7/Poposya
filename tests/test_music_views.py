"""Music views: SearchSelect, LikedListView (пагинация/запуск), PlayerView
(interaction_check и кнопки управления)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.music.entities import LikedTrack, Track
from src.infrastructure.discord.cogs.music.views import (
    LikedListView,
    PlayerView,
    QueueView,
    SearchSelect,
    SearchView,
)


def make_track(vid="a", title="Песня", duration=200):
    return Track(
        video_id=vid,
        title=title,
        url=f"https://youtu.be/{vid}",
        duration=duration,
        requested_by=1,
        uploader="Artist",
    )


def make_liked(vid, title=None):
    return LikedTrack(
        user_id=1,
        video_id=vid,
        title=title or vid,
        uploader="Artist",
        duration=180,
        liked_at=__import__("datetime").datetime.now(),
    )


def make_interaction(user_id=1, in_voice=True):
    interaction = MagicMock()
    interaction.guild_id = 10
    interaction.user = SimpleNamespace(
        id=user_id,
        display_name="Гость",
        voice=SimpleNamespace(channel=MagicMock()) if in_voice else None,
    )
    interaction.guild = MagicMock()
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.data = {}
    return interaction


# --- SearchSelect / SearchView ---------------------------------------------


async def test_search_select_enqueues_chosen():
    enqueue = AsyncMock(return_value=True)
    tracks = [make_track("a", "Первый"), make_track("b", "Второй")]
    select = SearchSelect(enqueue, tracks, to_front=False)
    select._values = ["1"]  # выбрали второй
    interaction = make_interaction()
    await select.callback(interaction)
    interaction.response.edit_message.assert_awaited_once()
    enqueue.assert_awaited_once()
    assert enqueue.await_args.args[1] == [tracks[1]]


def test_search_view_builds_options():
    view = SearchView(AsyncMock(), [make_track("a"), make_track("b")])
    select = view.children[0]
    assert len(select.options) == 2


# --- LikedListView ----------------------------------------------------------


def make_liked_view(n=25, owner_id=1):
    resolve = SimpleNamespace(execute=AsyncMock())
    enqueue = AsyncMock(return_value=True)
    owner = SimpleNamespace(id=owner_id, display_name="Хозяин")
    tracks = [make_liked(f"v{i}", f"Track {i}") for i in range(n)]
    return LikedListView(resolve, enqueue, owner, tracks), resolve, enqueue


def test_liked_total_pages():
    view, *_ = make_liked_view(n=25)
    assert view.total_pages == 3  # 25 / 10 -> 3 страницы


def test_liked_build_embed_own_vs_other():
    view, *_ = make_liked_view(owner_id=1)
    own = view.build_embed(viewer_id=1)
    assert "Твои лайки" in own.title
    other = view.build_embed(viewer_id=2)
    assert "Лайки Хозяин" in other.title


async def test_liked_next_and_prev_buttons():
    view, *_ = make_liked_view(n=25)
    interaction = make_interaction()
    assert view.page == 0
    await view.next_button.callback(interaction)
    assert view.page == 1
    await view.prev_button.callback(interaction)
    assert view.page == 0


async def test_liked_play_select_requires_voice():
    view, *_ = make_liked_view()
    interaction = make_interaction(in_voice=False)
    await view.play_select.callback(interaction)
    assert "голосовой канал" in interaction.response.send_message.await_args.args[0]


async def test_liked_play_select_resolve_fail():
    view, resolve, _ = make_liked_view()
    resolve.execute.return_value = None
    interaction = make_interaction()
    view.play_select._values = ["v0"]
    await view.play_select.callback(interaction)
    assert "не смогла оживить" in interaction.followup.send.await_args.args[0].lower()


async def test_liked_play_select_success():
    view, resolve, enqueue = make_liked_view()
    resolve.execute.return_value = make_track("v0", "Оживлённый")
    interaction = make_interaction()
    view.play_select._values = ["v0"]
    await view.play_select.callback(interaction)
    enqueue.assert_awaited_once()
    assert "Добавила" in interaction.followup.send.await_args.args[0]


# --- QueueView --------------------------------------------------------------


def make_queue_player(n_queue=25, current=True):
    player = MagicMock()
    player.current = make_track("cur", "Текущий") if current else None
    player.queue = [make_track(f"q{i}", f"Трек {i}") for i in range(n_queue)]
    return player


def test_queue_view_embed_shows_current_and_requester():
    view = QueueView(make_queue_player(n_queue=3))
    embed = view.build_embed()
    assert "Текущий" in embed.description
    assert "<@1>" in embed.description  # заказчик
    assert "Стр. 1/1" in embed.footer.text


def test_queue_view_pagination():
    view = QueueView(make_queue_player(n_queue=25))  # 3 страницы
    assert view.total_pages == 3
    assert view.prev_button.disabled is True  # на первой ← выключен
    assert view.next_button.disabled is False


async def test_queue_view_next_prev():
    view = QueueView(make_queue_player(n_queue=25))
    interaction = make_interaction()
    await view.next_button.callback(interaction)
    assert view.page == 1
    await view.prev_button.callback(interaction)
    assert view.page == 0


def test_queue_view_empty_queue():
    view = QueueView(make_queue_player(n_queue=0))
    embed = view.build_embed()
    assert "пусто" in embed.description


# --- PlayerView -------------------------------------------------------------


def make_player_view(player=None):
    service = MagicMock()
    service.get_player.return_value = player
    service.cleanup = AsyncMock()
    lyrics = MagicMock()
    lyrics.toggle = AsyncMock()
    on_like = AsyncMock()
    return PlayerView(service, lyrics, on_like), service, lyrics, on_like


def make_player():
    player = MagicMock()
    player.previous = AsyncMock(return_value=True)
    player.toggle_pause = AsyncMock()
    player.skip = AsyncMock()
    player.cycle_repeat = AsyncMock()
    player.change_volume = AsyncMock()
    player.shuffle = AsyncMock()
    player.queue = [make_track("a"), make_track("b")]
    return player


async def test_player_view_check_no_player():
    view, *_ = make_player_view(player=None)
    interaction = make_interaction()
    assert await view.interaction_check(interaction) is False
    assert "не активен" in interaction.response.send_message.await_args.args[0]


async def test_player_view_check_like_bypasses_voice():
    view, *_ = make_player_view(player=make_player())
    interaction = make_interaction(in_voice=False)
    interaction.data = {"custom_id": "music:like"}
    assert await view.interaction_check(interaction) is True


async def test_player_view_check_requires_same_channel():
    player = make_player()
    view, service, *_ = make_player_view(player=player)
    interaction = make_interaction(in_voice=True)
    # бот в другом канале
    interaction.guild.voice_client = SimpleNamespace(channel=MagicMock())
    assert await view.interaction_check(interaction) is False


async def test_player_view_check_ok_same_channel():
    player = make_player()
    view, *_ = make_player_view(player=player)
    interaction = make_interaction(in_voice=True)
    shared = interaction.user.voice.channel
    interaction.guild.voice_client = SimpleNamespace(channel=shared)
    assert await view.interaction_check(interaction) is True


async def test_player_buttons_delegate_to_player():
    player = make_player()
    view, *_ = make_player_view(player=player)
    interaction = make_interaction()
    await view.pause_button.callback(interaction)
    player.toggle_pause.assert_awaited_once()
    await view.next_button.callback(interaction)
    player.skip.assert_awaited_once()
    await view.repeat_button.callback(interaction)
    player.cycle_repeat.assert_awaited_once()
    await view.volume_down_button.callback(interaction)
    await view.volume_up_button.callback(interaction)
    assert player.change_volume.await_count == 2


async def test_player_prev_button_empty_history():
    player = make_player()
    player.previous = AsyncMock(return_value=False)
    view, *_ = make_player_view(player=player)
    interaction = make_interaction()
    await view.prev_button.callback(interaction)
    assert "История пуста" in interaction.followup.send.await_args.args[0]


async def test_player_stop_button_cleans_up():
    view, service, *_ = make_player_view(player=make_player())
    interaction = make_interaction()
    await view.stop_button.callback(interaction)
    service.cleanup.assert_awaited_once()


async def test_player_shuffle_empty_queue():
    player = make_player()
    player.queue = [make_track("a")]  # меньше 2 — нечего мешать
    view, *_ = make_player_view(player=player)
    interaction = make_interaction()
    await view.shuffle_button.callback(interaction)
    assert "нечего" in interaction.response.send_message.await_args.args[0]
    player.shuffle.assert_not_awaited()


async def test_player_shuffle_runs():
    player = make_player()
    view, *_ = make_player_view(player=player)
    interaction = make_interaction()
    await view.shuffle_button.callback(interaction)
    player.shuffle.assert_awaited_once()


async def test_player_lyrics_and_like_delegate():
    view, service, lyrics, on_like = make_player_view(player=make_player())
    interaction = make_interaction()
    await view.lyrics_button.callback(interaction)
    lyrics.toggle.assert_awaited_once()
    await view.like_button.callback(interaction)
    on_like.assert_awaited_once()
