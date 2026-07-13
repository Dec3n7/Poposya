import time
from collections import defaultdict, deque

from src.application.interfaces.rate_limiter import IRateLimiter


class InMemoryRateLimiter(IRateLimiter):
    """Скользящее окно на ключ. Достаточно для одного процесса;
    Redis-реализация появится при межпроцессном масштабировании."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def try_acquire(self, key: str, limit: int, window_seconds: int = 3600) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True
