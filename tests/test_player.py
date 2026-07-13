import asyncio

import pytest

from src.application.interfaces.audio_source import IAudioSource
from src.application.interfaces.voice_connection import IVoiceConnection
from src.application.music.player import GuildPlayer
from src.domain.music.entities import RepeatMode, Track
from src.infrastructure.events.in_memory_bus import InMemoryEventBus


def make_track(video_id: str, title: str | None = None) -> Track:
    return Track(
        video_id=video_id,
        title=title or video_id,
        url=f"https://youtube.com/watch?v={video_id}",
        duration=180,
        requested_by=1,
    )


class FakeVoice(IVoiceConnection):
    def __init__(self):
        self.played: list[str] = []
        self.volume: float | None = None
        self.paused = False
        self.disconnected = False
        self._on_finished = None

    async def play(self, stream_url, volume, on_finished, headers=None):
        self.played.append(stream_url)
        self.volume = volume
        self._on_finished = on_finished

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop(self):
        if self._on_finished is not None:
            cb, self._on_finished = self._on_finished, None
            cb(None)

    def finish_naturally(self):
        """Имитирует естественное окончание трека."""
        self.stop()

    def set_volume(self, volume):
        self.volume = volume

    async def disconnect(self):
        self.disconnected = True


class FakeAudio(IAudioSource):
    async def search(self, query, requested_by, limit=5):
        return []

    async def resolve(self, url, requested_by, playlist_limit=50):
        return []

    async def get_stream_url(self, track):
        return f"stream:{track.video_id}"


@pytest.fixture
def voice():
    return FakeVoice()


@pytest.fixture
def player(voice):
    return GuildPlayer(
        guild_id=1,
        audio_source=FakeAudio(),
        voice=voice,
        event_bus=InMemoryEventBus(),
        volume=0.5,
    )


async def settle():
    # даём отработать задачам, запланированным через call_soon_threadsafe
    for _ in range(5):
        await asyncio.sleep(0)


async def test_enqueue_starts_playback(player, voice):
    await player.enqueue([make_track("a"), make_track("b")])
    assert player.current.video_id == "a"
    assert list(player.queue) == [make_track("b")]
    assert voice.played == ["stream:a"]


async def test_skip_advances_queue(player, voice):
    await player.enqueue([make_track("a"), make_track("b")])
    await player.skip()
    await settle()
    assert player.current.video_id == "b"
    assert player.history[-1].video_id == "a"


async def test_natural_finish_advances(player, voice):
    await player.enqueue([make_track("a"), make_track("b")])
    voice.finish_naturally()
    await settle()
    assert player.current.video_id == "b"


async def test_repeat_one_replays_on_natural_finish(player, voice):
    await player.enqueue([make_track("a"), make_track("b")])
    player.repeat = RepeatMode.ONE
    voice.finish_naturally()
    await settle()
    assert player.current.video_id == "a"
    assert voice.played == ["stream:a", "stream:a"]


async def test_repeat_one_does_not_block_manual_skip(player, voice):
    await player.enqueue([make_track("a"), make_track("b")])
    player.repeat = RepeatMode.ONE
    await player.skip()
    await settle()
    assert player.current.video_id == "b"


async def test_repeat_all_requeues_finished_track(player, voice):
    await player.enqueue([make_track("a"), make_track("b")])
    player.repeat = RepeatMode.ALL
    voice.finish_naturally()
    await settle()
    assert player.current.video_id == "b"
    assert list(player.queue) == [make_track("a")]


async def test_previous_returns_to_history(player, voice):
    await player.enqueue([make_track("a"), make_track("b")])
    await player.skip()  # теперь играет b, в истории a
    await settle()
    assert await player.previous()
    await settle()
    assert player.current.video_id == "a"
    # b вернулся в начало очереди
    assert list(player.queue)[0].video_id == "b"


async def test_previous_without_history(player):
    await player.enqueue([make_track("a")])
    assert not await player.previous()


async def test_volume_clamped(player, voice):
    for _ in range(30):
        await player.change_volume(+0.1)
    assert player.volume == 2.0
    for _ in range(40):
        await player.change_volume(-0.1)
    assert player.volume == 0.0


async def test_prefetch_caches_next_track_stream(player, voice):
    await player.enqueue([make_track("a"), make_track("b")])
    await settle()
    # поток следующего трека уже в кэше — смена треков без паузы на yt-dlp
    assert player._cached_stream_url("b") == "stream:b"
    voice.finish_naturally()
    await settle()
    assert player.current.video_id == "b"
    assert voice.played == ["stream:a", "stream:b"]


async def test_queue_end_calls_on_idle(player, voice):
    idle_called = False

    async def on_idle():
        nonlocal idle_called
        idle_called = True

    player.on_idle = on_idle
    await player.enqueue([make_track("a")])
    voice.finish_naturally()
    await settle()
    assert player.current is None
    assert idle_called
