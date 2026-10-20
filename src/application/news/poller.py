"""Минимальный общий хелпер фонового опроса новостей.

Владеет только таймером: старт по `on_ready`, интервал, изоляция исключений
такта и остановка. Что именно опрашивать — решает вертикаль через `tick`.
Это не движок источников и не повод рефакторить существующие трекеры.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class NewsPoller:
    def __init__(
        self,
        *,
        name: str,
        interval_seconds: int,
        tick: Callable[[], Awaitable[None]],
    ):
        self._name = name
        self._interval = max(1, int(interval_seconds))
        self._tick = tick
        self._task: asyncio.Task | None = None

    @property
    def started(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.started:
            return
        self._task = asyncio.create_task(self._run(), name=f"news-poller-{self._name}")
        logger.info("%s: цикл опроса запущен (интервал %d c)", self._name, self._interval)

    def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self._tick()
            except Exception:
                logger.exception("%s: опрос упал на такте — продолжаю", self._name)
