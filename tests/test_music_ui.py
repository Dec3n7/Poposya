"""Музыкальный UI-модуль: форматирование, кэш текстов, сессия, радио-состояние."""

import asyncio

from src.domain.music.entities import Track
from src.infrastructure.discord.cogs.music.formatting import (
    block_index,
    fmt_duration,
    parse_duration,
    progress_bar,
    trim,
)
from src.infrastructure.discord.cogs.music.lyrics import LyricsService
from src.infrastructure.discord.cogs.music.session import GuildMusicSession

# --- форматирование ---


def test_fmt_duration():
    assert fmt_duration(None) == "🔴 эфир"
    assert fmt_duration(65) == "1:05"
    assert fmt_duration(3600) == "1:00:00"
    assert fmt_duration(3725) == "1:02:05"
    assert fmt_duration(0) == "0:00"


def test_parse_duration():
    assert parse_duration("83") == 83  # голые секунды
    assert parse_duration("1:23") == 83  # м:с
    assert parse_duration("1:02:03") == 3723  # ч:м:с
    assert parse_duration(" 0:00 ") == 0  # обрезка пробелов
    # мусор и отрицательные — None (для внятного отказа /seek)
    assert parse_duration("abc") is None
    assert parse_duration("1:2:3:4") is None
    assert parse_duration("-5") is None
    assert parse_duration("") is None


def test_progress_bar():
    assert progress_bar(10, 0) == "▬" * 14  # эфир: без позиции
    assert progress_bar(0, 100).startswith("🔘")
    assert progress_bar(100, 100).endswith("🔘")  # позиция зажата в границы
    assert progress_bar(50, 100).count("🔘") == 1


def test_trim():
    assert trim("абв", 10) == "абв"
    assert trim("абвгд", 3) == "аб…"
    assert len(trim("x" * 100, 20)) == 20


def test_block_index():
    blocks = [(0.0, ["a"]), (10.0, ["b"]), (20.0, ["c"])]
    assert block_index(blocks, -1.0) == -1  # трек ещё не дошёл до текста
    assert block_index(blocks, 0.0) == 0
    assert block_index(blocks, 15.0) == 1
    assert block_index(blocks, 99.0) == 2
    assert block_index([], 5.0) == -1


# --- кэш текстов ---


def make_track(video_id: str) -> Track:
    return Track(
        video_id=video_id,
        title=f"title-{video_id}",
        url=f"https://youtube.com/watch?v={video_id}",
        duration=180,
        requested_by=1,
    )


class FakeLyricsClient:
    def __init__(self):
        self.calls = 0

    async def find_both(self, title, uploader):
        self.calls += 1
        return (f"[00:01.00] {title}", title)


class FakeSettings:
    music_lyrics_offset = 0.0
    music_progress_interval = 10


def make_lyrics_service(client=None):
    tasks = []

    def spawn(coro):
        tasks.append(asyncio.ensure_future(coro))

    service = LyricsService(
        client or FakeLyricsClient(),
        FakeSettings(),
        get_session=lambda guild_id: None,
        spawn=spawn,
    )
    return service, tasks


async def test_lyrics_cached_after_first_request():
    client = FakeLyricsClient()
    service, _ = make_lyrics_service(client)
    track = make_track("a")
    first = await service.get(track)
    second = await service.get(track)
    assert first == second
    assert client.calls == 1  # второй раз — из кэша


async def test_lyrics_cache_evicts_oldest():
    client = FakeLyricsClient()
    service, _ = make_lyrics_service(client)
    for i in range(101):
        await service.get(make_track(f"v{i}"))
    assert client.calls == 101
    await service.get(make_track("v0"))  # самый старый вытеснен — новый запрос
    assert client.calls == 102
    await service.get(make_track("v100"))  # свежий ещё в кэше
    assert client.calls == 102


async def test_prefetch_deduplicates():
    client = FakeLyricsClient()
    service, tasks = make_lyrics_service(client)
    track = make_track("a")
    service.prefetch(track)
    service.prefetch(track)  # уже в pending — второй запуск не создаётся
    assert len(tasks) == 1
    await asyncio.gather(*tasks)
    assert client.calls == 1
    service.prefetch(track)  # уже в кэше — тоже не создаётся
    assert len(tasks) == 1


# --- сессия ---


async def test_session_cancel_tasks():
    async def forever():
        await asyncio.sleep(3600)

    session = GuildMusicSession(player=object())
    session.updater_task = asyncio.create_task(forever())
    session.idle_task = asyncio.create_task(forever())
    session.karaoke_task = asyncio.create_task(forever())
    session.cancel_tasks()
    await asyncio.sleep(0)
    assert session.updater_task.cancelled()
    assert session.idle_task.cancelled()
    assert session.karaoke_task.cancelled()


async def test_session_cancel_tasks_tolerates_none():
    GuildMusicSession(player=object()).cancel_tasks()  # не падает на пустых полях
