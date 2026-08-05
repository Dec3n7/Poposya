"""YtDlpAudioSource с замоканным _extract (без yt-dlp/сети), AudioCache на диске,
чистые функции lyrics (clean_track_title/build_queries/parse_lrc/group_blocks)."""

import time
from unittest.mock import AsyncMock

import pytest

from src.domain.music.entities import Track
from src.domain.music.exceptions import TrackResolveError
from src.infrastructure.audio import ytdlp_source
from src.infrastructure.audio.cache import AudioCache
from src.infrastructure.audio.lyrics import (
    build_queries,
    clean_track_title,
    group_blocks,
    parse_lrc,
)
from src.infrastructure.audio.ytdlp_source import YtDlpAudioSource


@pytest.fixture(autouse=True)
def _bypass_ssrf(monkeypatch):
    # resolve() зовёт SSRF-щит (реальный DNS-резолв); тесты дают нерезолвимые
    # хосты (http://list, http://one) — глушим щит, кроме отдельного теста ниже.
    monkeypatch.setattr(ytdlp_source, "assert_public_url", AsyncMock())


def make_track(video_id="abc", duration=180):
    return Track(
        video_id=video_id,
        title="T",
        url=f"https://youtu.be/{video_id}",
        duration=duration,
        requested_by=1,
    )


# --- YtDlpAudioSource: _entry_to_track --------------------------------------


def test_entry_to_track_full():
    track = YtDlpAudioSource._entry_to_track(
        {
            "id": "vid1",
            "title": "Песня",
            "duration": 200.7,
            "webpage_url": "https://youtu.be/vid1",
            "uploader": "Chan",
        },
        requested_by=5,
    )
    assert track.video_id == "vid1"
    assert track.title == "Песня"
    assert track.duration == 200  # int
    assert track.requested_by == 5
    assert track.uploader == "Chan"
    assert track.thumbnail.endswith("vid1/hqdefault.jpg")


def test_entry_to_track_defaults_and_live():
    track = YtDlpAudioSource._entry_to_track({"id": "", "duration": None}, requested_by=1)
    assert track.title == "Без названия"
    assert track.duration is None and track.is_live
    assert track.url == "https://www.youtube.com/watch?v="
    assert track.thumbnail is None


# --- YtDlpAudioSource: search / resolve / get_stream_url --------------------


async def test_search_maps_entries(monkeypatch):
    src = YtDlpAudioSource()

    async def fake_extract(target, flat):
        assert target == "ytsearch3:queen"
        return {
            "entries": [
                {"id": "a", "title": "A", "duration": 100},
                None,
                {"id": "b", "title": "B", "duration": 200},
            ]
        }

    monkeypatch.setattr(src, "_extract", fake_extract)
    tracks = await src.search("queen", requested_by=1, limit=3)
    assert [t.video_id for t in tracks] == ["a", "b"]  # None отфильтрован


async def test_resolve_playlist(monkeypatch):
    src = YtDlpAudioSource()

    async def fake_extract(target, flat):
        return {"entries": [{"id": str(i), "title": str(i), "duration": 60} for i in range(5)]}

    monkeypatch.setattr(src, "_extract", fake_extract)
    tracks = await src.resolve("http://list", requested_by=1, playlist_limit=3)
    assert [t.video_id for t in tracks] == ["0", "1", "2"]  # усечено до лимита


async def test_resolve_single(monkeypatch):
    src = YtDlpAudioSource()

    async def fake_extract(target, flat):
        return {"id": "solo", "title": "Solo", "duration": 90}

    monkeypatch.setattr(src, "_extract", fake_extract)
    tracks = await src.resolve("http://one", requested_by=1)
    assert len(tracks) == 1 and tracks[0].video_id == "solo"


async def test_resolve_empty_raises(monkeypatch):
    src = YtDlpAudioSource()

    async def fake_extract(target, flat):
        return None

    monkeypatch.setattr(src, "_extract", fake_extract)
    with pytest.raises(TrackResolveError):
        await src.resolve("http://x", requested_by=1)


async def test_resolve_blocks_internal_url(monkeypatch):
    """SSRF-щит: ссылка во внутреннюю сеть не доходит до yt-dlp (_extract)."""
    from src.infrastructure.net.ssrf import SsrfError

    src = YtDlpAudioSource()
    extract = AsyncMock()
    monkeypatch.setattr(src, "_extract", extract)
    monkeypatch.setattr(
        ytdlp_source, "assert_public_url", AsyncMock(side_effect=SsrfError("во внутреннюю сеть"))
    )
    with pytest.raises(TrackResolveError, match="внутреннюю сеть"):
        await src.resolve("http://169.254.169.254/latest/meta-data/", requested_by=1)
    extract.assert_not_called()


async def test_get_stream_url_direct(monkeypatch):
    src = YtDlpAudioSource()

    async def fake_extract(target, flat):
        return {"url": "http://stream.direct"}

    monkeypatch.setattr(src, "_extract", fake_extract)
    assert await src.get_stream_url(make_track()) == "http://stream.direct"


async def test_get_stream_url_from_formats(monkeypatch):
    src = YtDlpAudioSource()

    async def fake_extract(target, flat):
        return {
            "formats": [
                {"acodec": "none", "url": "http://video-only"},
                {"acodec": "opus", "url": "http://audio"},
            ]
        }

    monkeypatch.setattr(src, "_extract", fake_extract)
    # берётся с конца — первый годный аудиоформат
    assert await src.get_stream_url(make_track()) == "http://audio"


async def test_get_stream_url_remembers_metadata(monkeypatch):
    src = YtDlpAudioSource()

    async def fake_extract(target, flat):
        return {"url": "http://s", "view_count": 999, "upload_date": "20200101"}

    monkeypatch.setattr(src, "_extract", fake_extract)
    assert src.track_meta("abc") is None  # ещё не играли
    await src.get_stream_url(make_track("abc"))
    meta = src.track_meta("abc")
    assert meta["view_count"] == 999 and meta["upload_date"] == "20200101"


def test_fmt_count():
    from src.infrastructure.discord.cogs.music.formatting import fmt_count

    assert fmt_count(None) is None
    assert fmt_count(0) is None
    assert fmt_count(500) == "500"
    assert fmt_count(15_300) == "15.3K"
    assert fmt_count(1_234_567) == "1.2M"
    assert fmt_count(2_000_000) == "2M"


async def test_get_stream_url_none_raises(monkeypatch):
    src = YtDlpAudioSource()

    async def fake_extract(target, flat):
        return {"formats": []}

    monkeypatch.setattr(src, "_extract", fake_extract)
    with pytest.raises(TrackResolveError):
        await src.get_stream_url(make_track())


# --- cached_path / download без кэша ----------------------------------------


def test_cached_path_no_cache():
    assert YtDlpAudioSource().cached_path(make_track()) is None


async def test_download_no_cache_returns_none():
    assert await YtDlpAudioSource().download(make_track()) is None


async def test_download_live_returns_none(tmp_path):
    cache = AudioCache(tmp_path, max_bytes=10**9)
    src = YtDlpAudioSource(cache=cache)
    assert await src.download(make_track(duration=None)) is None


def test_cached_path_hits_cache(tmp_path):
    cache = AudioCache(tmp_path, max_bytes=10**9)
    (tmp_path / "abc.webm").write_bytes(b"x")
    src = YtDlpAudioSource(cache=cache)
    assert src.cached_path(make_track("abc")).endswith("abc.webm")


def test_opts_with_cookies_from_browser():
    src = YtDlpAudioSource(cookies_from_browser="firefox")
    opts = src._opts_with_cookies({})
    assert opts["cookiesfrombrowser"] == ("firefox",)


def test_opts_with_cookies_file(tmp_path):
    # применяется только существующий файл (гард от падения yt-dlp на кривом пути)
    f = tmp_path / "cookies.txt"
    f.write_text("x")
    src = YtDlpAudioSource(cookies_file=str(f))
    opts = src._opts_with_cookies({})
    assert opts["cookiefile"] == str(f)


# --- AudioCache -------------------------------------------------------------


def test_cache_find_and_skip_temp(tmp_path):
    cache = AudioCache(tmp_path, max_bytes=10**9)
    assert cache.find("missing") is None
    (tmp_path / "v1.part").write_bytes(b"x")  # недокачанный — не кэш
    assert cache.find("v1") is None
    (tmp_path / "v1.m4a").write_bytes(b"x")
    assert cache.find("v1").name == "v1.m4a"


def test_cache_find_refreshes_mtime(tmp_path):
    cache = AudioCache(tmp_path, max_bytes=10**9)
    f = tmp_path / "v1.m4a"
    f.write_bytes(b"x")
    old = time.time() - 10_000
    import os

    os.utime(f, (old, old))
    cache.find("v1")  # должно обновить mtime
    assert f.stat().st_mtime > old + 1000


def test_cache_prune_evicts_oldest(tmp_path):
    cache = AudioCache(tmp_path, max_bytes=150)  # влезает ~1.5 файла по 100 байт
    import os

    for i, name in enumerate(["old.m4a", "mid.m4a", "new.m4a"]):
        p = tmp_path / name
        p.write_bytes(b"x" * 100)
        os.utime(p, (1000 + i, 1000 + i))  # new — самый свежий
    cache.prune()
    names = {p.name for p in tmp_path.iterdir()}
    assert "new.m4a" in names  # свежий остаётся
    assert "old.m4a" not in names  # старейший вытеснен


def test_cache_creates_directory(tmp_path):
    target = tmp_path / "nested" / "cache"
    AudioCache(target, max_bytes=1)
    assert target.exists()


# --- lyrics pure functions --------------------------------------------------


def test_clean_track_title_strips_junk():
    assert clean_track_title("Lil Peep -- 16 Lines (Official Video)") == "Lil Peep 16 Lines"
    # одиночный дефис — не разделитель (только `--`, `|`, `/`, тире), скобки/скобы убираются
    assert clean_track_title("Artist | Song [HD] {Lyrics}") == "Artist Song"
    assert clean_track_title("Artist - Song [HD] {Lyrics}") == "Artist - Song"


def test_clean_track_title_keeps_jp_content():
    # японские кавычки убираются, содержимое остаётся
    assert "YOASOBI" in clean_track_title("YOASOBI「アイドル」")


def test_build_queries_variants():
    queries = build_queries("Song (Official Video)", uploader="Artist - Topic")
    assert "Song" in queries
    assert any("Artist" in q for q in queries)
    # сырой заголовок добавляется как последний кандидат
    assert "Song (Official Video)" in queries


def test_build_queries_dedups_uploader_in_title():
    queries = build_queries("Artist Song", uploader="Artist")
    # канал уже в чистом заголовке — не дублируем
    assert not any(q == "Artist Artist Song" for q in queries)


def test_parse_lrc_sorts_by_time():
    lrc = "[00:10.00] вторая\n[00:05.00] первая\nмусор\n[01:00.00] третья"
    parsed = parse_lrc(lrc)
    assert [text for _, text in parsed] == ["первая", "вторая", "третья"]
    assert parsed[2][0] == 60.0  # 01:00 -> 60 секунд


def test_parse_lrc_skips_empty_content():
    assert parse_lrc("[00:01.00]   \n[00:02.00] есть") == [(2.0, "есть")]


def test_group_blocks():
    lines = [(float(i), f"l{i}") for i in range(5)]
    blocks = group_blocks(lines, size=2)
    assert len(blocks) == 3
    assert blocks[0] == (0.0, ["l0", "l1"])
    assert blocks[2] == (4.0, ["l4"])  # хвост короче size
