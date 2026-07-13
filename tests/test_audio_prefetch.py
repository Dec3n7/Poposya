"""Кэш аудио на диске и закачка треков наперёд (лечит заикания стрима)."""

import asyncio
import os
import time

from src.application.music.player import GuildPlayer
from src.infrastructure.audio.cache import AudioCache
from src.infrastructure.events.in_memory_bus import InMemoryEventBus
from tests.test_player import FakeAudio, FakeVoice, make_track


# --- AudioCache ---


def test_cache_find_ignores_partial_downloads(tmp_path):
    cache = AudioCache(tmp_path / "audio", max_bytes=10_000)
    (cache.directory / "abc.webm.part").write_bytes(b"half")
    assert cache.find("abc") is None
    (cache.directory / "abc.webm").write_bytes(b"full")
    found = cache.find("abc")
    assert found is not None and found.name == "abc.webm"


def test_cache_prune_evicts_oldest(tmp_path):
    cache = AudioCache(tmp_path / "audio", max_bytes=250)
    now = time.time()
    for i, video_id in enumerate(("old", "mid", "new")):
        path = cache.directory / f"{video_id}.webm"
        path.write_bytes(b"x" * 100)
        os.utime(path, (now + i, now + i))  # old — самый старый
    cache.prune()
    names = {p.name for p in cache.directory.iterdir()}
    assert names == {"mid.webm", "new.webm"}  # 300 байт > 250 — old вытеснен


def test_cache_find_refreshes_mtime(tmp_path):
    cache = AudioCache(tmp_path / "audio", max_bytes=10_000)
    path = cache.directory / "abc.webm"
    path.write_bytes(b"data")
    os.utime(path, (1_000_000, 1_000_000))  # давнее прошлое
    cache.find("abc")
    assert path.stat().st_mtime > 1_000_000  # LRU: обращение освежило файл


# --- закачка наперёд в GuildPlayer ---


class DownloadingFakeAudio(FakeAudio):
    """Источник с кэшем: download кладёт файл в словарь, cached_path читает."""

    def __init__(self, prefill: list[str] | None = None):
        self.files: dict[str, str] = {v: f"file:{v}" for v in (prefill or [])}
        self.downloads: list[str] = []
        self.fail_ids: set[str] = set()

    def cached_path(self, track):
        return self.files.get(track.video_id)

    async def download(self, track):
        self.downloads.append(track.video_id)
        if track.video_id in self.fail_ids:
            return None
        self.files[track.video_id] = f"file:{track.video_id}"
        return self.files[track.video_id]


def make_player(audio, voice, prefetch_files=3):
    return GuildPlayer(
        guild_id=1,
        audio_source=audio,
        voice=voice,
        event_bus=InMemoryEventBus(),
        volume=0.5,
        prefetch_files=prefetch_files,
    )


async def settle():
    for _ in range(10):
        await asyncio.sleep(0)


async def test_downloads_next_tracks_not_current():
    audio, voice = DownloadingFakeAudio(), FakeVoice()
    player = make_player(audio, voice)
    await player.enqueue([make_track(v) for v in "abcde"])
    await settle()
    # текущий (a) стримится, качаются следующие 3 (b, c, d); e — за горизонтом
    assert audio.downloads == ["b", "c", "d"]
    assert voice.played == ["stream:a"]


async def test_plays_from_cache_when_downloaded():
    audio, voice = DownloadingFakeAudio(prefill=["a"]), FakeVoice()
    player = make_player(audio, voice)
    await player.enqueue([make_track("a"), make_track("b")])
    await settle()
    assert voice.played == ["file:a"]  # локальный файл вместо стрима
    voice.finish_naturally()
    await settle()
    assert voice.played == ["file:a", "file:b"]  # b успел скачаться наперёд


async def test_failed_download_falls_back_to_stream():
    audio, voice = DownloadingFakeAudio(), FakeVoice()
    audio.fail_ids.add("b")
    player = make_player(audio, voice)
    await player.enqueue([make_track("a"), make_track("b")])
    await settle()
    assert audio.downloads.count("b") == 1  # без повторных попыток
    voice.finish_naturally()
    await settle()
    assert voice.played == ["stream:a", "stream:b"]  # фолбэк на стрим


async def test_live_tracks_not_downloaded():
    audio, voice = DownloadingFakeAudio(), FakeVoice()
    player = make_player(audio, voice)
    live = make_track("live")
    live = type(live)(**{**live.__dict__, "duration": None})
    await player.enqueue([make_track("a"), live, make_track("c")])
    await settle()
    assert "live" not in audio.downloads
    assert "c" in audio.downloads


async def test_prefetch_zero_disables_downloads():
    audio, voice = DownloadingFakeAudio(), FakeVoice()
    player = make_player(audio, voice, prefetch_files=0)
    await player.enqueue([make_track(v) for v in "abc"])
    await settle()
    assert audio.downloads == []
