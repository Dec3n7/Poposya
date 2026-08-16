"""Изолированный сервис рендера карточек: HTML → PNG (Playwright + Chromium).

Вынесен из процесса бота в отдельный контейнер намеренно: браузер под
--no-sandbox — самая крупная attack surface системы, и его компрометация НЕ
должна давать доступ к Discord-токену, БД или сети. Контейнер сидит на internal-
сети без выхода в интернет, принимает только POST /render с самодостаточным HTML
(аватар уже вшит data-URI отправителем) и отдаёт PNG. Никаких секретов внутри.

Браузер поднимается один раз и переиспользуется (старт Chromium дорогой), на
каждую карточку — лёгкая новая страница, которая всегда закрывается.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from aiohttp import web
from render_cache import RenderCache, render_key

if TYPE_CHECKING:
    # Только для аннотаций: playwright есть лишь в образе renderer. Держать его
    # импорт на верхнем уровне значило бы требовать playwright везде, где просто
    # импортируют этот модуль (тесты кэша/admission на фейк-рендере, CI бота без
    # renderer-зависимостей). Реальный импорт — лениво в _Renderer._ensure().
    from playwright.async_api import Browser, Playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("renderer")

# предохранители на вход: сервис внутренний, но вход всё равно валидируем
_MAX_HTML_BYTES = 4 * 1024 * 1024
_MAX_DIM = 4000


def _cache_from_env() -> RenderCache:
    """Границы кэша — из окружения, правятся без пересборки образа. Нули
    (`RENDER_CACHE_MAX_BYTES=0`) полностью выключают кэш — аварийный тумблер."""
    entries = int(os.getenv("RENDER_CACHE_MAX_ENTRIES", "512"))
    mbytes = int(os.getenv("RENDER_CACHE_MAX_BYTES", str(256 * 1024 * 1024)))
    return RenderCache(max_entries=entries, max_bytes=mbytes)


# A3: admission control. Замер (perf-baseline.md) показал near-OOM при 8
# одновременных рендерах — Chromium открывает страницу на каждый. Кап конкуррент-
# ности держит память в узде: лишние запросы ждут слот короткое время, и если не
# дождались — быстрый 503, по которому бот показывает embed-фолбэк (штатный путь
# при сбое renderer), а не копит страницы до OOM.
_MAX_CONCURRENCY = int(os.getenv("RENDER_MAX_CONCURRENCY", "3"))
_QUEUE_TIMEOUT = float(os.getenv("RENDER_QUEUE_TIMEOUT_SECONDS", "10"))


class _Renderer:
    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> Browser:
        # Ленивый импорт: playwright требуется только при реальном подъёме
        # Chromium, а не при импорте модуля (см. TYPE_CHECKING-блок выше).
        from playwright.async_api import async_playwright

        async with self._lock:
            if self._browser is None:
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(
                    # --disable-dev-shm-usage: держать shared memory в /tmp, а не в
                    # /dev/shm (64 МБ по умолчанию под docker — Chromium там падает).
                    # Заодно совместимо с read_only-rootfs: пишем только в tmpfs /tmp.
                    args=[
                        "--no-sandbox",
                        "--disable-gpu",
                        "--hide-scrollbars",
                        "--disable-dev-shm-usage",
                    ]
                )
                logger.info("Chromium поднят")
        assert self._browser is not None
        return self._browser

    async def render(self, html: str, width: int, height: int, scale: int) -> bytes:
        browser = await self._ensure()
        page = await browser.new_page(
            viewport={"width": width, "height": height}, device_scale_factor=scale
        )
        try:
            await page.set_content(html, wait_until="networkidle")
            return await page.screenshot(type="png")
        finally:
            await page.close()

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None


def create_app(
    renderer: object | None = None,
    cache: RenderCache | None = None,
    max_concurrency: int | None = None,
    queue_timeout: float | None = None,
) -> web.Application:
    # renderer/cache/лимиты инъектируемы: тест подставляет фейк-рендер (без
    # Chromium), свой кэш и малый кап; прод берёт настоящий браузер и env.
    renderer = _Renderer() if renderer is None else renderer
    cache = _cache_from_env() if cache is None else cache
    slots = max_concurrency if max_concurrency is not None else _MAX_CONCURRENCY
    timeout = queue_timeout if queue_timeout is not None else _QUEUE_TIMEOUT
    gate = asyncio.Semaphore(slots)

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "cache": cache.stats()})

    async def render(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "bad body"}, status=400)
        html = body.get("html")
        try:
            width = int(body.get("width", 0))
            height = int(body.get("height", 0))
            scale = int(body.get("scale", 2))
        except (TypeError, ValueError):
            return web.json_response({"error": "bad dimensions"}, status=400)
        if not isinstance(html, str) or not html:
            return web.json_response({"error": "html required"}, status=400)
        if not (0 < width <= _MAX_DIM) or not (0 < height <= _MAX_DIM):
            return web.json_response({"error": "dimensions out of range"}, status=400)
        if not (1 <= scale <= 4):
            return web.json_response({"error": "scale out of range"}, status=400)

        # A2: content-addressed кэш. Ключ по HTML+размерам+scale; на попадании
        # отдаём готовый PNG, минуя Chromium целиком.
        key = render_key(html, width, height, scale)
        cached = cache.get(key)
        if cached is not None:
            # HIT не трогает браузер — слот конкуррентности ему не нужен.
            return web.Response(body=cached, content_type="image/png", headers={"X-Cache": "HIT"})

        # A3: ждём слот ограниченное время; не дождались — 503 (→ embed-фолбэк).
        try:
            await asyncio.wait_for(gate.acquire(), timeout=timeout)
        except TimeoutError:
            return web.json_response({"error": "renderer busy"}, status=503)
        try:
            png = await renderer.render(html, width, height, scale)
        except Exception:
            logger.exception("рендер упал")
            return web.json_response({"error": "render failed"}, status=500)
        finally:
            gate.release()
        cache.put(key, png)
        return web.Response(body=png, content_type="image/png", headers={"X-Cache": "MISS"})

    app = web.Application(client_max_size=_MAX_HTML_BYTES + 64 * 1024)
    app.router.add_get("/health", health)
    app.router.add_post("/render", render)

    async def _cleanup(_: web.Application) -> None:
        close = getattr(renderer, "close", None)
        if close is not None:
            await close()

    app.on_cleanup.append(_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8090)
