"""Именованные одноразовые таймеры на asyncio.

Запускает callback в момент `when`; повторный schedule с тем же ключом
заменяет прежний таймер. Просроченный момент (when в прошлом) выполняется
сразу — поэтому восстановление после рестарта работает: коги сами
перечитывают due-элементы из БД и переназначают таймеры. Исключение внутри
callback логируется и не роняет остальные таймеры."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class DeferredScheduler:
    def __init__(self, name: str = "scheduler"):
        self._name = name
        self._timers: dict[str, asyncio.Task] = {}

    def schedule(self, key: str, when: datetime, callback: Callable[[], Awaitable[None]]) -> None:
        self.cancel(key)

        async def run() -> None:
            delay = (when - datetime.now(UTC)).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await callback()
            except Exception:
                logger.exception("Таймер упал", extra={"scheduler": self._name, "timer": key})

        task = asyncio.create_task(run())
        self._timers[key] = task
        task.add_done_callback(lambda _: self._timers.pop(key, None))

    def cancel(self, key: str) -> None:
        task = self._timers.pop(key, None)
        if task is not None:
            task.cancel()

    def cancel_all(self) -> None:
        for task in self._timers.values():
            task.cancel()
        self._timers.clear()

    def __len__(self) -> int:
        return len(self._timers)
