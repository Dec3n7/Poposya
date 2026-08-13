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

from aiohttp import web
from playwright.async_api import Browser, Playwright, async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("renderer")

# предохранители на вход: сервис внутренний, но вход всё равно валидируем
_MAX_HTML_BYTES = 4 * 1024 * 1024
_MAX_DIM = 4000


class _Renderer:
    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> Browser:
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


def create_app() -> web.Application:
    renderer = _Renderer()

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

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
        try:
            png = await renderer.render(html, width, height, scale)
        except Exception:
            logger.exception("рендер упал")
            return web.json_response({"error": "render failed"}, status=500)
        return web.Response(body=png, content_type="image/png")

    app = web.Application(client_max_size=_MAX_HTML_BYTES + 64 * 1024)
    app.router.add_get("/health", health)
    app.router.add_post("/render", render)

    async def _cleanup(_: web.Application) -> None:
        await renderer.close()

    app.on_cleanup.append(_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8090)
