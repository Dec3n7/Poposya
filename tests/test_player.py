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
        self.seeks: list[float] = []  # seek_seconds каждого play
        self.volume: float | None = None
        self.paused = False
        self.disconnected = False
        self._on_finished = None

    async def play(self, stream_url, volume, on_finished, headers=None, seek_seconds=0.0):
        self.played.append(stream_url)
        self.seeks.append(seek_seconds)
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


class FailingAudio(FakeAudio):
    """Источник, который не может выдать поток для перечисленных video_id."""

    def __init__(self, dead: set[str]):
        self._dead = dead

    async def get_stream_url(self, track):
        from src.domain.music.exceptions import TrackResolveError

        if track.video_id in self._dead:
            raise TrackResolveError("мертвяк")
        return f"stream:{track.video_id}"


async def test_failed_track_notifies_and_advances(voice):
    player = GuildPlayer(
        guild_id=1,
        audio_source=FailingAudio(dead={"a"}),
        voice=voice,
        event_bus=InMemoryEventBus(),
        volume=0.5,
    )
    failed: list[tuple[str, str]] = []

    async def on_failed(track, reason):
        failed.append((track.video_id, reason))

    player.on_track_failed = on_failed
    await player.enqueue([make_track("a"), make_track("b")])
    # «a» мёртв: хук позвали, плеер перешёл к «b», в очереди «a» не застрял
    assert failed == [("a", "мертвяк")]
    assert player.current.video_id == "b"
    assert voice.played == ["stream:b"]


async def test_failed_hook_absent_still_advances(voice):
    # без хука провал по-прежнему просто пропускается (обратная совместимость)
    player = GuildPlayer(
        guild_id=1,
        audio_source=FailingAudio(dead={"a"}),
        voice=voice,
        event_bus=InMemoryEventBus(),
        volume=0.5,
    )
    await player.enqueue([make_track("a"), make_track("b")])
    assert player.current.video_id == "b"


async def test_remove_at_takes_track_out_of_queue(player, voice):
    await player.enqueue([make_track("a"), make_track("b"), make_track("c")])
    # играет «a», в очереди [b, c]; убираем №2 (c)
    removed = await player.remove_at(2)
    assert removed.video_id == "c"
    assert [t.video_id for t in player.queue] == ["b"]
    assert player.current.video_id == "a"  # текущий не тронут


async def test_remove_at_out_of_range_returns_none(player, voice):
    await player.enqueue([make_track("a"), make_track("b")])
    assert await player.remove_at(5) is None
    assert await player.remove_at(0) is None  # 1-based, нуля нет
    assert [t.video_id for t in player.queue] == ["b"]  # очередь цела


async def test_seek_restarts_same_track_at_offset(player, voice):
    await player.enqueue([make_track("a"), make_track("b")])
    assert await player.seek(90) is True
    await settle()
    # тот же трек, без продвижения очереди; ffmpeg получил offset 90
    assert player.current.video_id == "a"
    assert list(player.queue) == [make_track("b")]  # «b» не тронут
    assert voice.seeks[-1] == 90  # ffmpeg -ss 90
    assert 89 <= player.elapsed() <= 91  # тайминг сдвинут на позицию


async def test_seek_rejects_out_of_range(player, voice):
    await player.enqueue([make_track("a")])  # duration=180
    assert await player.seek(999) is False
    assert await player.seek(-5) is False
    assert player.current.video_id == "a"  # ничего не перезапустилось


async def test_seek_rejects_live_stream(voice):
    player = GuildPlayer(
        guild_id=1,
        audio_source=FakeAudio(),
        voice=voice,
        event_bus=InMemoryEventBus(),
        volume=0.5,
    )
    live = Track(video_id="live", title="эфир", url="u", duration=None, requested_by=1)
    await player.enqueue([live])
    assert await player.seek(10) is False  # у эфира нет длительности


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
