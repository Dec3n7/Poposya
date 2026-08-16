"""Content-addressed кэш renderer (рычаг A2, docs/plans/scale-300-guilds.md).

Кэш живёт в образе renderer (renderer/render_cache.py + server.py), не в src, но
это чистый stdlib-модуль без Chromium — поэтому кладём каталог renderer на путь и
тестируем его настоящим кодом, а не переписанной копией. Интеграционный тест
поднимает настоящее aiohttp-приложение renderer с фейк-рендером (без браузера) и
доказывает, что на повторный идентичный HTML Chromium не зовётся вовсе.
"""

import asyncio
import sys
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

_RENDERER_DIR = Path(__file__).resolve().parents[1] / "renderer"
if str(_RENDERER_DIR) not in sys.path:
    sys.path.insert(0, str(_RENDERER_DIR))

import server  # noqa: E402  (после вставки пути)
from render_cache import RenderCache, render_key  # noqa: E402

# ── ключ ────────────────────────────────────────────────────────────────────


def test_key_stable_for_same_input():
    a = render_key("<h1>x</h1>", 300, 150, 2)
    b = render_key("<h1>x</h1>", 300, 150, 2)
    assert a == b


def test_key_sensitive_to_every_field():
    base = render_key("<h1>x</h1>", 300, 150, 2)
    assert render_key("<h1>y</h1>", 300, 150, 2) != base  # html
    assert render_key("<h1>x</h1>", 301, 150, 2) != base  # width
    assert render_key("<h1>x</h1>", 300, 151, 2) != base  # height
    assert render_key("<h1>x</h1>", 300, 150, 3) != base  # scale


def test_key_no_boundary_collision():
    # склейка размеров не должна давать один ключ для 30x1 и 3x01 и т.п.
    assert render_key("h", 30, 1, 2) != render_key("h", 3, 1, 2)
    assert render_key("h", 1, 23, 2) != render_key("h", 12, 3, 2)


# ── LRU / границы ─────────────────────────────────────────────────────────────


def test_get_put_hit_miss():
    c = RenderCache(max_entries=4, max_bytes=1024)
    assert c.get("k") is None  # miss
    c.put("k", b"png")
    assert c.get("k") == b"png"  # hit
    assert c.hits == 1 and c.misses == 1


def test_lru_eviction_by_entries():
    c = RenderCache(max_entries=2, max_bytes=1 << 20)
    c.put("a", b"1")
    c.put("b", b"2")
    c.get("a")  # трогаем a → теперь b самый старый
    c.put("c", b"3")  # переполнение по числу → вытесняется b
    assert c.get("a") == b"1"
    assert c.get("c") == b"3"
    assert c.get("b") is None


def test_eviction_by_bytes():
    c = RenderCache(max_entries=100, max_bytes=10)
    c.put("a", b"12345")  # 5
    c.put("b", b"12345")  # 5 → всего 10, влезает
    c.put("c", b"1")  # переполнение по байтам → вытесняется a (старейший)
    assert c.get("a") is None
    assert c.get("b") == b"12345"
    assert c.get("c") == b"1"


def test_oversized_value_not_cached():
    c = RenderCache(max_entries=10, max_bytes=4)
    c.put("big", b"12345")  # крупнее всего бюджета → не кэшируем
    assert c.get("big") is None
    assert c.stats()["entries"] == 0


def test_disabled_cache_stores_nothing():
    c = RenderCache(max_entries=0, max_bytes=0)
    assert not c.enabled
    c.put("k", b"png")
    assert c.get("k") is None


def test_put_same_key_updates_and_keeps_bytes_consistent():
    c = RenderCache(max_entries=10, max_bytes=100)
    c.put("k", b"aaaa")  # 4
    c.put("k", b"bb")  # перезапись → байты должны стать 2, не 6
    assert c.get("k") == b"bb"
    assert c.stats()["bytes"] == 2
    assert c.stats()["entries"] == 1


def test_stats_hit_rate():
    c = RenderCache()
    c.put("k", b"x")
    c.get("k")  # hit
    c.get("miss")  # miss
    s = c.stats()
    assert s["hits"] == 1 and s["misses"] == 1 and s["hit_rate"] == 0.5


# ── интеграция с приложением renderer (фейк-рендер, без Chromium) ─────────────


class _FakeRenderer:
    """Считает реальные рендеры. Возвращает детерминированный «PNG» по входу,
    чтобы проверить, что HIT отдаёт ровно те же байты, что и первый MISS."""

    def __init__(self) -> None:
        self.calls = 0

    async def render(self, html: str, width: int, height: int, scale: int) -> bytes:
        self.calls += 1
        return b"\x89PNG" + f"{html}:{width}x{height}@{scale}".encode()

    async def close(self) -> None:
        pass


async def _client(renderer, cache, **kw) -> TestClient:
    client = TestClient(TestServer(server.create_app(renderer=renderer, cache=cache, **kw)))
    await client.start_server()
    return client


async def test_identical_request_hits_cache_and_skips_render():
    fake = _FakeRenderer()
    client = await _client(fake, RenderCache())
    try:
        payload = {"html": "<h1>карточка</h1>", "width": 300, "height": 150, "scale": 2}
        r1 = await client.post("/render", json=payload)
        b1 = await r1.read()
        r2 = await client.post("/render", json=payload)
        b2 = await r2.read()
    finally:
        await client.close()

    assert r1.headers["X-Cache"] == "MISS"
    assert r2.headers["X-Cache"] == "HIT"
    assert b1 == b2  # HIT отдаёт те же байты
    assert fake.calls == 1  # Chromium позван ровно один раз на два запроса


async def test_distinct_html_renders_each():
    fake = _FakeRenderer()
    client = await _client(fake, RenderCache())
    try:
        await client.post("/render", json={"html": "<a>", "width": 10, "height": 10, "scale": 2})
        await client.post("/render", json={"html": "<b>", "width": 10, "height": 10, "scale": 2})
    finally:
        await client.close()
    assert fake.calls == 2  # разный HTML — два рендера


async def test_health_reports_cache_stats():
    fake = _FakeRenderer()
    cache = RenderCache()
    client = await _client(fake, cache)
    try:
        payload = {"html": "<h1>x</h1>", "width": 10, "height": 10, "scale": 2}
        await client.post("/render", json=payload)  # miss
        await client.post("/render", json=payload)  # hit
        resp = await client.get("/health")
        body = await resp.json()
    finally:
        await client.close()
    assert body["status"] == "ok"
    assert body["cache"]["hits"] == 1
    assert body["cache"]["misses"] == 1
    assert body["cache"]["entries"] == 1


# ── A3: admission control ─────────────────────────────────────────────────────


class _BlockingRenderer:
    """Рендер, который зависает до отмашки — держит слот конкуррентности занятым,
    чтобы проверить деградацию под насыщением."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def render(self, html: str, width: int, height: int, scale: int) -> bytes:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return b"\x89PNG"

    async def close(self) -> None:
        self.release.set()


async def test_saturated_renderer_returns_503_for_embed_fallback():
    blocker = _BlockingRenderer()
    # кэш выключен → оба запроса промахиваются и лезут за слотом; кап = 1
    client = await _client(blocker, RenderCache(0, 0), max_concurrency=1, queue_timeout=0.2)
    try:
        a = asyncio.create_task(
            client.post("/render", json={"html": "<a>", "width": 10, "height": 10, "scale": 2})
        )
        await asyncio.wait_for(blocker.started.wait(), timeout=2)  # слот занят
        r_busy = await client.post(
            "/render", json={"html": "<b>", "width": 10, "height": 10, "scale": 2}
        )
        assert r_busy.status == 503  # слота нет → быстрый отказ (бот покажет embed)
        blocker.release.set()
        r_ok = await a
        assert r_ok.status == 200  # первый по отмашке доезжает
    finally:
        blocker.release.set()
        await client.close()


async def test_cache_hit_bypasses_concurrency_gate():
    """HIT обслуживается даже когда единственный слот занят рендером: готовый PNG
    браузера не трогает, значит и слот ему не нужен."""
    blocker = _BlockingRenderer()
    client = await _client(blocker, RenderCache(), max_concurrency=1, queue_timeout=0.2)
    try:
        warm = {"html": "<warm>", "width": 10, "height": 10, "scale": 2}
        blocker.release.set()  # прогрев проходит без блокировки
        r_warm = await client.post("/render", json=warm)  # miss → кэшируется
        assert r_warm.headers["X-Cache"] == "MISS"
        assert blocker.calls == 1
        blocker.release.clear()  # следующий рендер зависнет
        blocker.started.clear()

        busy = asyncio.create_task(
            client.post("/render", json={"html": "<busy>", "width": 10, "height": 10, "scale": 2})
        )
        await asyncio.wait_for(blocker.started.wait(), timeout=2)  # слот занят рендером <busy>
        r_hit = await client.post("/render", json=warm)  # тот же <warm> → HIT, минуя слот
        assert r_hit.status == 200
        assert r_hit.headers["X-Cache"] == "HIT"
        blocker.release.set()
        await busy
    finally:
        blocker.release.set()
        await client.close()
