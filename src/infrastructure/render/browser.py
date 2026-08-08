"""HTML → PNG рендер карточек через Playwright + Chromium.

Карточки (`/rank`, ачивки) — это HTML/CSS: glassmorphism, градиенты, свечения и
SVG-иконки, чего Pillow чисто не даёт. Браузер поднимается один раз на процесс и
переиспользуется: старт Chromium дорогой (~сотни мс), а на каждую карточку
заводится лёгкая новая страница.

Блокировка на старте — чтобы параллельные первые запросы не подняли два браузера.
Сам рендер параллелится страницами и лончера не лочит.
"""

from __future__ import annotations

import asyncio
import logging

from playwright.async_api import Browser, Playwright, async_playwright

logger = logging.getLogger(__name__)


class CardRenderer:
    """Единый лончер Chromium. `start()` при старте бота, `close()` при остановке.

    `scale=2` — рендер в 2× и отдача чёткого PNG (ретина-качество в Discord)."""

    def __init__(self, scale: int = 2):
        self._scale = scale
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._start_lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._start_lock:
            if self._browser is not None:
                return
            pw = await async_playwright().start()
            self._pw = pw
            self._browser = await pw.chromium.launch(
                args=["--no-sandbox", "--disable-gpu", "--hide-scrollbars"]
            )
            logger.info("CardRenderer: Chromium поднят")

    async def _ensure(self) -> Browser:
        if self._browser is None:
            await self.start()
        assert self._browser is not None
        return self._browser

    async def render(self, html: str, width: int, height: int) -> bytes:
        """HTML документа фиксированного размера → PNG-байты. Страница закрывается
        всегда: утечка вкладок за часы работы съела бы память."""
        browser = await self._ensure()
        page = await browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=self._scale,
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
