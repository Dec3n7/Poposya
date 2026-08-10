"""Клиент сервиса рендера карточек (HTML → PNG).

Сам браузер (Playwright + Chromium под --no-sandbox) вынесен в ОТДЕЛЬНЫЙ
изолированный контейнер `renderer` — он самая крупная attack surface, и его
компрометация не должна давать доступ к Discord-токену, БД и сети. Здесь только
тонкий HTTP-клиент: бот строит самодостаточный HTML (аватар уже вшит data-URI) и
просит renderer растеризовать его в PNG.

Интерфейс намеренно тот же, что был у прежнего внутрипроцессного рендера
(`start()`/`render()`/`close()`) — коги и контейнер менять не пришлось. Если
renderer недоступен, `render()` бросает исключение, а вызывающий ког показывает
текстовый фолбэк (как и раньше при сбое браузера).
"""

from __future__ import annotations

import logging

import aiohttp

logger = logging.getLogger(__name__)


class CardRenderer:
    """HTTP-клиент к сервису renderer. `scale=2` — просим рендер в 2× (ретина-
    качество PNG в Discord); фактический device_scale_factor применяет renderer."""

    def __init__(self, url: str, scale: int = 2, timeout: float = 20.0):
        self._url = url.rstrip("/")
        self._scale = scale
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        """Поднимает переиспользуемую HTTP-сессию. Renderer стартует сам в своём
        контейнере — здесь браузер больше не поднимается."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
            logger.info("CardRenderer: клиент renderer готов (%s)", self._url)

    async def _ensure(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            await self.start()
        assert self._session is not None
        return self._session

    async def render(self, html: str, width: int, height: int) -> bytes:
        """Самодостаточный HTML → PNG-байты через сервис renderer. Бросает при
        недоступности/ошибке renderer — верхний ког отвечает текстом."""
        session = await self._ensure()
        payload = {"html": html, "width": width, "height": height, "scale": self._scale}
        async with session.post(f"{self._url}/render", json=payload) as resp:
            if resp.status != 200:
                detail = (await resp.text())[:200]
                raise RuntimeError(f"renderer вернул {resp.status}: {detail}")
            return await resp.read()

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
