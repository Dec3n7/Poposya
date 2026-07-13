"""Доп. покрытие GuildPlayer: громкость, shuffle, повтор-циклы, пауза,
тайминг, стоп-очистка, all_tracks, enqueue в начало, скип мёртвого трека."""
import asyncio

import pytest

from src.application.interfaces.audio_source import IAudioSource
from src.application.interfaces.voice_connection import IVoiceConnection
from src.application.music.player import GuildPlayer
from src.domain.music.entities import RepeatMode, Track
from src.domain.music.exceptions import TrackResolveError
from src.infrastructure.events.in_memory_bus import InMemoryEventBus


def make_track(video_id, duration=180):
    return Track(
        video_id=video_id, title=video_id,
        url=f"https://youtube.com/watch?v={video_id}",
        duration=duration, requested_by=1,
    )


class FakeVoice(IVoiceConnection):
    def __init__(self):
        self.played = []
        self.volume = None
        self.paused = False
        self.resumed = False
        self.disconnected = False
        self.stopped = 0
        self._on_finished = None

    async def play(self, stream_url, volume, on_finished, headers=None):
        self.played.append(stream_url)
        self.volume = volume
        self._on_finished = on_finished

    def pause(self):
        self.paused = True

    def resume(self):
        self.resumed = True
        self.paused = False

    def stop(self):
        self.stopped += 1
        if self._on_finished is not None:
            cb, self._on_finished = self._on_finished, None
            cb(None)

    def set_volume(self, volume):
        self.volume = volume

    async def disconnect(self):
        self.disconnected = True


class FakeAudio(IAudioSource):
    def __init__(self, dead=()):
        self.dead = set(dead)  # video_id, для которых get_stream_url падает

    async def search(self, query, requested_by, limit=5):
        return []

    async def resolve(self, url, requested_by, playlist_limit=50):
        return []

    async def get_stream_url(self, track):
        if track.video_id in self.dead:
            raise TrackResolveError("dead")
        return f"stream:{track.video_id}"


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


def make_player(voice, audio=None):
    return GuildPlayer(
        guild_id=1,
        audio_source=audio or FakeAudio(),
        voice=voice,
        event_bus=InMemoryEventBus(),
        volume=0.5,
        prefetch_files=0,  # без фоновых закачек — детерминизм
    )


@pytest.fixture
def voice():
    return FakeVoice()


async def test_set_volume_absolute_and_clamp(voice):
    p = make_player(voice)
    await p.set_volume(1.5)
    assert p.volume == 1.5 and voice.volume == 1.5
    await p.set_volume(9.0)
    assert p.volume == 2.0
    await p.set_volume(-1.0)
    assert p.volume == 0.0


async def test_change_volume_returns_value(voice):
    p = make_player(voice)
    assert await p.change_volume(0.25) == 0.75


async def test_cycle_repeat_order(voice):
    p = make_player(voice)
    assert await p.cycle_repeat() == RepeatMode.ONE
    assert await p.cycle_repeat() == RepeatMode.ALL
    assert await p.cycle_repeat() == RepeatMode.OFF


async def test_toggle_pause_tracks_state(voice):
    p = make_player(voice)
    await p.enqueue([make_track("a")])
    await p.toggle_pause()
    assert p.is_paused and voice.paused
    await p.toggle_pause()
    assert not p.is_paused and voice.resumed


async def test_toggle_pause_noop_when_idle(voice):
    p = make_player(voice)
    await p.toggle_pause()  # ничего не играет
    assert not p.is_paused


async def test_pause_freezes_elapsed(voice, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr("src.application.music.player.time.monotonic", lambda: clock["t"])
    p = make_player(voice)
    await p.enqueue([make_track("a")])
    clock["t"] += 10
    assert p.elapsed() == 10
    await p.toggle_pause()
    clock["t"] += 100  # на паузе время не идёт
    assert p.elapsed() == 10
    await p.toggle_pause()
    clock["t"] += 5
    assert p.elapsed() == 15


async def test_elapsed_zero_before_start(voice):
    p = make_player(voice)
    assert p.elapsed() == 0.0
    assert p.elapsed_precise() == 0.0


async def test_all_tracks_includes_current(voice):
    p = make_player(voice)
    await p.enqueue([make_track("a"), make_track("b"), make_track("c")])
    ids = [t.video_id for t in p.all_tracks()]
    assert ids == ["a", "b", "c"]  # current + очередь


async def test_enqueue_front_jumps_queue(voice):
    p = make_player(voice)
    await p.enqueue([make_track("a"), make_track("b")])
    await p.enqueue([make_track("z")], front=True)
    assert [t.video_id for t in p.queue] == ["z", "b"]


async def test_shuffle_preserves_set(voice):
    p = make_player(voice)
    await p.enqueue([make_track(str(i)) for i in range(10)])  # играет "0"
    await p.shuffle()
    assert sorted(t.video_id for t in p.queue) == sorted(str(i) for i in range(1, 10))


async def test_skip_dead_track_advances(voice):
    audio = FakeAudio(dead={"bad"})
    p = make_player(voice, audio)
    await p.enqueue([make_track("bad"), make_track("good")])
    # "bad" не проигрывается — плеер проматывает к "good"
    assert p.current.video_id == "good"
    assert voice.played == ["stream:good"]


async def test_all_dead_queue_goes_idle(voice):
    audio = FakeAudio(dead={"x", "y"})
    p = make_player(voice, audio)
    await p.enqueue([make_track("x"), make_track("y")])
    assert p.current is None


async def test_stop_and_clear_resets(voice):
    p = make_player(voice)
    await p.enqueue([make_track("a"), make_track("b")])
    await p.stop_and_clear()
    assert p.current is None
    assert list(p.queue) == []
    assert p.history == []
    assert voice.disconnected


async def test_skip_when_idle_does_not_crash(voice):
    p = make_player(voice)
    await p.enqueue([make_track("a")])
    voice.stop()  # доиграл до конца
    await settle()
    assert p.current is None
    await p.skip()  # skip на пустой очереди не падает
    assert p.current is None


async def test_state_changed_hook_called(voice):
    p = make_player(voice)
    calls = []

    async def hook():
        calls.append(1)

    p.on_state_changed = hook
    await p.enqueue([make_track("a")])
    assert calls  # хук обновления сообщения дёрнулся
