"""Тесты HTTP-провайдеров с заглушками aiohttp (без сети): Groq, Spotify oEmbed,
LRCLIB, TMDB, Кинопоиск + фолбэк-поиск фильмов."""

import aiohttp
import pytest

from src.application.interfaces.ai_provider import ChatMessage
from src.domain.ai_chat.exceptions import AIProviderError
from src.infrastructure.ai.groq_provider import GroqAIProvider
from src.infrastructure.audio.lyrics import LrclibLyricsClient
from src.infrastructure.audio.spotify import SpotifyLinkResolver
from src.infrastructure.cinema.kinopoisk import KinopoiskClient
from src.infrastructure.cinema.provider import FallbackMovieSearch, MovieInfo
from src.infrastructure.cinema.tmdb import TmdbClient
from tests.aiohttp_fakes import FakeResponse, FakeSession, patch_session

MSGS = [ChatMessage(role="user", content="привет")]


# --- Groq -------------------------------------------------------------------


async def test_groq_success(monkeypatch):
    resp = FakeResponse(200, json_data={"choices": [{"message": {"content": "ответ Groq"}}]})
    capture = {}
    monkeypatch.setattr(
        "src.infrastructure.ai.groq_provider.aiohttp.ClientSession",
        lambda **kw: FakeSession(response=resp, capture=capture),
    )
    prov = GroqAIProvider(api_key="secret", model="llama")
    text = await prov.generate("system", MSGS)
    assert text == "ответ Groq"
    # payload собран правильно, ключ в заголовке
    assert capture["json"]["model"] == "llama"
    assert capture["json"]["messages"][0] == {"role": "system", "content": "system"}
    assert capture["headers"]["Authorization"] == "Bearer secret"


async def test_groq_429_is_retryable_with_retry_after(monkeypatch):
    resp = FakeResponse(429, text_data="rate limited", headers={"retry-after": "7"})
    monkeypatch.setattr(
        "src.infrastructure.ai.groq_provider.aiohttp.ClientSession",
        lambda **kw: FakeSession(response=resp),
    )
    prov = GroqAIProvider(api_key="k")
    with pytest.raises(AIProviderError) as exc:
        await prov.generate("s", MSGS)
    assert exc.value.retryable is True
    assert exc.value.retry_after == 7.0


async def test_groq_500_retryable_400_not(monkeypatch):
    for status, retryable in [(500, True), (503, True), (400, False), (401, False)]:
        monkeypatch.setattr(
            "src.infrastructure.ai.groq_provider.aiohttp.ClientSession",
            lambda s=status, **kw: FakeSession(response=FakeResponse(s, text_data="err")),
        )
        prov = GroqAIProvider(api_key="k")
        with pytest.raises(AIProviderError) as exc:
            await prov.generate("s", MSGS)
        assert exc.value.retryable is retryable


async def test_groq_network_error_retryable(monkeypatch):
    monkeypatch.setattr(
        "src.infrastructure.ai.groq_provider.aiohttp.ClientSession",
        lambda **kw: FakeSession(exc=aiohttp.ClientError("boom")),
    )
    prov = GroqAIProvider(api_key="k")
    with pytest.raises(AIProviderError) as exc:
        await prov.generate("s", MSGS)
    assert exc.value.retryable is True


async def test_groq_malformed_response(monkeypatch):
    monkeypatch.setattr(
        "src.infrastructure.ai.groq_provider.aiohttp.ClientSession",
        lambda **kw: FakeSession(response=FakeResponse(200, json_data={"nope": 1})),
    )
    prov = GroqAIProvider(api_key="k")
    with pytest.raises(AIProviderError, match="Неожиданный ответ"):
        await prov.generate("s", MSGS)


async def test_groq_close_is_safe():
    prov = GroqAIProvider(api_key="k")
    await prov.close()  # сессии не было — не должно падать


# --- Spotify ----------------------------------------------------------------


def test_spotify_link_detection():
    assert SpotifyLinkResolver.is_spotify_link("https://open.spotify.com/track/xyz")
    assert not SpotifyLinkResolver.is_spotify_link("https://youtube.com/watch?v=1")
    assert SpotifyLinkResolver.is_track_link("https://open.spotify.com/track/xyz")
    assert not SpotifyLinkResolver.is_track_link("https://open.spotify.com/album/xyz")


async def test_spotify_oembed_builds_query(monkeypatch):
    resp = FakeResponse(200, json_data={"title": "16 Lines", "author_name": "Lil Peep"})
    patch_session(monkeypatch, "src.infrastructure.audio.spotify", response=resp)
    q = await SpotifyLinkResolver().search_query_for("https://open.spotify.com/track/x")
    assert q == "Lil Peep 16 Lines"


async def test_spotify_oembed_no_title(monkeypatch):
    patch_session(
        monkeypatch,
        "src.infrastructure.audio.spotify",
        response=FakeResponse(200, json_data={"author_name": "A"}),
    )
    assert await SpotifyLinkResolver().search_query_for("url") is None


async def test_spotify_oembed_http_error(monkeypatch):
    patch_session(monkeypatch, "src.infrastructure.audio.spotify", response=FakeResponse(404))
    assert await SpotifyLinkResolver().search_query_for("url") is None


async def test_spotify_oembed_network_error(monkeypatch):
    patch_session(monkeypatch, "src.infrastructure.audio.spotify", exc=aiohttp.ClientError("down"))
    assert await SpotifyLinkResolver().search_query_for("url") is None


# --- LRCLIB -----------------------------------------------------------------


async def test_lyrics_find_plain_and_synced(monkeypatch):
    resp = FakeResponse(
        200, json_data=[{"plainLyrics": "текст песни", "syncedLyrics": "[00:01.00] строка"}]
    )
    patch_session(monkeypatch, "src.infrastructure.audio.lyrics", response=resp)
    client = LrclibLyricsClient()
    assert await client.find_lyrics("Song") == "текст песни"
    assert "строка" in await client.find_synced("Song")


async def test_lyrics_find_both(monkeypatch):
    resp = FakeResponse(200, json_data=[{"plainLyrics": "p", "syncedLyrics": "s"}])
    patch_session(monkeypatch, "src.infrastructure.audio.lyrics", response=resp)
    synced, plain = await LrclibLyricsClient().find_both("Song")
    assert (synced, plain) == ("s", "p")


async def test_lyrics_empty_results(monkeypatch):
    patch_session(
        monkeypatch, "src.infrastructure.audio.lyrics", response=FakeResponse(200, json_data=[])
    )
    assert await LrclibLyricsClient().find_lyrics("Song") is None


async def test_lyrics_http_error(monkeypatch):
    patch_session(monkeypatch, "src.infrastructure.audio.lyrics", response=FakeResponse(500))
    assert await LrclibLyricsClient().find_lyrics("Song") is None


async def test_lyrics_network_error(monkeypatch):
    patch_session(monkeypatch, "src.infrastructure.audio.lyrics", exc=aiohttp.ClientError("down"))
    assert await LrclibLyricsClient().find_lyrics("Song") is None


# --- TMDB -------------------------------------------------------------------


def test_tmdb_disabled_without_key():
    assert TmdbClient("").enabled is False


async def test_tmdb_disabled_returns_empty():
    assert await TmdbClient("   ").search("query") == []


async def test_tmdb_parses_results(monkeypatch):
    resp = FakeResponse(
        200,
        json_data={
            "results": [
                {
                    "id": 27205,
                    "title": "Начало",
                    "release_date": "2010-07-16",
                    "overview": "сон во сне",
                    "poster_path": "/abc.jpg",
                },
                {
                    "id": 2,
                    "original_title": "Fallback",
                    "release_date": "",
                    "overview": "",
                    "poster_path": "",
                },
            ]
        },
    )
    capture = {}
    patch_session(monkeypatch, "src.infrastructure.cinema.tmdb", response=resp, capture=capture)
    results = await TmdbClient("key").search("начало", limit=5)
    assert len(results) == 2
    assert results[0] == MovieInfo(
        27205, "Начало", 2010, "сон во сне", "https://image.tmdb.org/t/p/w500/abc.jpg"
    )
    # второй — без даты/постера
    assert results[1].year is None and results[1].poster_url == ""
    assert results[1].title == "Fallback"
    assert capture["params"]["api_key"] == "key"


async def test_tmdb_http_error(monkeypatch):
    patch_session(monkeypatch, "src.infrastructure.cinema.tmdb", response=FakeResponse(401))
    assert await TmdbClient("key").search("q") == []


async def test_tmdb_network_error(monkeypatch):
    patch_session(monkeypatch, "src.infrastructure.cinema.tmdb", exc=aiohttp.ClientError("down"))
    assert await TmdbClient("key").search("q") == []


# --- Kinopoisk --------------------------------------------------------------


def test_kinopoisk_disabled_without_key():
    assert KinopoiskClient("").enabled is False


async def test_kinopoisk_parses_docs(monkeypatch):
    resp = FakeResponse(
        200,
        json_data={
            "docs": [
                {
                    "id": 301,
                    "name": "Матрица",
                    "year": 1999,
                    "description": "красная таблетка",
                    "poster": {"url": "http://p/1.jpg"},
                },
                {"id": 0, "alternativeName": "Alt", "shortDescription": "кратко"},
            ]
        },
    )
    capture = {}
    patch_session(
        monkeypatch, "src.infrastructure.cinema.kinopoisk", response=resp, capture=capture
    )
    results = await KinopoiskClient("tok").search("матрица", limit=5)
    assert results[0] == MovieInfo(301, "Матрица", 1999, "красная таблетка", "http://p/1.jpg")
    assert results[1].title == "Alt" and results[1].year is None
    assert capture["headers"]["X-API-KEY"] == "tok"


async def test_kinopoisk_http_error(monkeypatch):
    patch_session(monkeypatch, "src.infrastructure.cinema.kinopoisk", response=FakeResponse(403))
    assert await KinopoiskClient("tok").search("q") == []


# --- FallbackMovieSearch ----------------------------------------------------


class StubSearch:
    def __init__(self, enabled=True, results=None, raise_exc=None):
        self._enabled = enabled
        self._results = results or []
        self._raise = raise_exc
        self.called = 0

    @property
    def enabled(self):
        return self._enabled

    async def search(self, query, limit=5):
        self.called += 1
        if self._raise:
            raise self._raise
        return self._results


async def test_fallback_uses_primary_when_it_returns():
    primary = StubSearch(results=[MovieInfo(1, "A", None, "", "")])
    secondary = StubSearch(results=[MovieInfo(2, "B", None, "", "")])
    fb = FallbackMovieSearch(primary, secondary)
    results = await fb.search("q")
    assert results[0].title == "A"
    assert secondary.called == 0


async def test_fallback_switches_when_primary_empty():
    primary = StubSearch(results=[])
    secondary = StubSearch(results=[MovieInfo(2, "B", None, "", "")])
    fb = FallbackMovieSearch(primary, secondary)
    assert (await fb.search("q"))[0].title == "B"


async def test_fallback_switches_when_primary_raises():
    primary = StubSearch(raise_exc=RuntimeError("boom"))
    secondary = StubSearch(results=[MovieInfo(2, "B", None, "", "")])
    fb = FallbackMovieSearch(primary, secondary)
    assert (await fb.search("q"))[0].title == "B"


async def test_fallback_skips_disabled_primary():
    primary = StubSearch(enabled=False)
    secondary = StubSearch(results=[MovieInfo(2, "B", None, "", "")])
    fb = FallbackMovieSearch(primary, secondary)
    assert (await fb.search("q"))[0].title == "B"
    assert primary.called == 0


def test_fallback_enabled_reflects_children():
    assert (
        FallbackMovieSearch(StubSearch(enabled=False), StubSearch(enabled=False)).enabled is False
    )
    assert FallbackMovieSearch(StubSearch(enabled=False), StubSearch(enabled=True)).enabled is True


async def test_fallback_all_empty_returns_empty():
    fb = FallbackMovieSearch(StubSearch(results=[]), StubSearch(results=[]))
    assert await fb.search("q") == []
