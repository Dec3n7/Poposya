"""Тесты устойчивости AI-цепочки: circuit breaker, retry/backoff, fallback,
rate limiter. Без сети — inner-провайдер подменяется заглушкой."""
import asyncio

import pytest

from src.application.interfaces.ai_provider import ChatMessage, IAIProvider
from src.domain.ai_chat.exceptions import AIProviderError
from src.infrastructure.ai.circuit_breaker import CircuitBreakerAIProvider
from src.infrastructure.ai.rate_limiter import InMemoryRateLimiter
from src.infrastructure.ai.resilient import FallbackAIProvider, ResilientAIProvider


class FakeProvider(IAIProvider):
    """Программируемая заглушка. behaviours — очередь: строка => вернуть,
    Exception => бросить. Пустая очередь => повтор последнего поведения."""

    def __init__(self, behaviours):
        self._behaviours = list(behaviours)
        self.calls = 0
        self.closed = False

    async def generate(self, system_prompt, messages):
        self.calls += 1
        item = self._behaviours[min(self.calls - 1, len(self._behaviours) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self):
        self.closed = True


MSGS = [ChatMessage(role="user", content="привет")]


async def _noop_sleep(_delay):
    """Мгновенный sleep, чтобы ретраи не тормозили тесты."""
    return None


# --- CircuitBreaker ---------------------------------------------------------

async def test_cb_passes_through_on_success():
    inner = FakeProvider(["ok"])
    cb = CircuitBreakerAIProvider(inner)
    assert await cb.generate("sys", MSGS) == "ok"
    assert cb._state == "CLOSED"


async def test_cb_opens_after_threshold():
    inner = FakeProvider([AIProviderError("boom")])
    cb = CircuitBreakerAIProvider(inner, failure_threshold=3)
    for _ in range(3):
        with pytest.raises(AIProviderError):
            await cb.generate("sys", MSGS)
    assert cb._state == "OPEN"
    # цепь открыта — запрос отклоняется мгновенно, inner не дёргается
    calls_before = inner.calls
    with pytest.raises(AIProviderError, match="circuit breaker"):
        await cb.generate("sys", MSGS)
    assert inner.calls == calls_before


async def test_cb_success_resets_failure_count():
    inner = FakeProvider([AIProviderError("boom"), AIProviderError("boom"), "ok"])
    cb = CircuitBreakerAIProvider(inner, failure_threshold=3)
    for _ in range(2):
        with pytest.raises(AIProviderError):
            await cb.generate("sys", MSGS)
    assert cb._failure_count == 2
    assert await cb.generate("sys", MSGS) == "ok"
    assert cb._failure_count == 0
    assert cb._state == "CLOSED"


async def test_cb_half_open_after_timeout_then_closes(monkeypatch):
    inner = FakeProvider([AIProviderError("boom"), "recovered"])
    cb = CircuitBreakerAIProvider(inner, failure_threshold=1, timeout=60)

    clock = {"t": 1000.0}
    monkeypatch.setattr("src.infrastructure.ai.circuit_breaker.time.monotonic", lambda: clock["t"])

    with pytest.raises(AIProviderError):
        await cb.generate("sys", MSGS)
    assert cb._state == "OPEN"

    # ещё в пределах timeout — по-прежнему отклоняется
    clock["t"] += 30
    with pytest.raises(AIProviderError, match="circuit breaker"):
        await cb.generate("sys", MSGS)

    # timeout истёк — HALF_OPEN → успех → CLOSED
    clock["t"] += 40
    assert await cb.generate("sys", MSGS) == "recovered"
    assert cb._state == "CLOSED"


async def test_cb_half_open_failure_reopens(monkeypatch):
    inner = FakeProvider([AIProviderError("boom")])
    cb = CircuitBreakerAIProvider(inner, failure_threshold=1, timeout=60)
    clock = {"t": 0.0}
    monkeypatch.setattr("src.infrastructure.ai.circuit_breaker.time.monotonic", lambda: clock["t"])

    with pytest.raises(AIProviderError):
        await cb.generate("sys", MSGS)
    clock["t"] += 100  # переход в HALF_OPEN на следующем вызове
    with pytest.raises(AIProviderError):
        await cb.generate("sys", MSGS)
    # проба в HALF_OPEN упала — снова OPEN
    assert cb._state == "OPEN"


async def test_cb_close_delegates():
    inner = FakeProvider(["ok"])
    cb = CircuitBreakerAIProvider(inner)
    await cb.close()
    assert inner.closed


# --- ResilientAIProvider (retry/backoff) -----------------------------------

async def test_resilient_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("src.infrastructure.ai.resilient.asyncio.sleep", _noop_sleep)
    inner = FakeProvider([AIProviderError("429", retryable=True), "ok"])
    prov = ResilientAIProvider(inner, attempts=3, base_delay=0.01)
    assert await prov.generate("sys", MSGS) == "ok"
    assert inner.calls == 2


async def test_resilient_gives_up_after_attempts(monkeypatch):
    monkeypatch.setattr("src.infrastructure.ai.resilient.asyncio.sleep", _noop_sleep)
    inner = FakeProvider([AIProviderError("boom", retryable=True)])
    prov = ResilientAIProvider(inner, attempts=3, base_delay=0.01)
    with pytest.raises(AIProviderError):
        await prov.generate("sys", MSGS)
    assert inner.calls == 3


async def test_resilient_does_not_retry_non_retryable():
    inner = FakeProvider([AIProviderError("400", retryable=False)])
    prov = ResilientAIProvider(inner, attempts=3)
    with pytest.raises(AIProviderError):
        await prov.generate("sys", MSGS)
    assert inner.calls == 1


async def test_resilient_respects_retry_after(monkeypatch):
    delays = []

    async def fake_sleep(d):
        delays.append(d)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    inner = FakeProvider([AIProviderError("429", retryable=True, retry_after=5.0), "ok"])
    prov = ResilientAIProvider(inner, attempts=2, base_delay=0.01)
    await prov.generate("sys", MSGS)
    # задержка не меньше retry_after, но ограничена _MAX_SINGLE_DELAY=8
    assert delays and 5.0 <= delays[0] <= 8.0


async def test_resilient_attempts_floor():
    prov = ResilientAIProvider(FakeProvider(["ok"]), attempts=0)
    assert prov._attempts == 1


async def test_resilient_close_delegates():
    inner = FakeProvider(["ok"])
    await ResilientAIProvider(inner).close()
    assert inner.closed


# --- FallbackAIProvider -----------------------------------------------------

async def test_fallback_uses_primary_when_ok():
    primary = FakeProvider(["primary"])
    fallback = FakeProvider(["fallback"])
    prov = FallbackAIProvider(primary, fallback)
    assert await prov.generate("sys", MSGS) == "primary"
    assert fallback.calls == 0


async def test_fallback_switches_on_primary_failure():
    primary = FakeProvider([AIProviderError("down")])
    fallback = FakeProvider(["fallback"])
    prov = FallbackAIProvider(primary, fallback)
    assert await prov.generate("sys", MSGS) == "fallback"


async def test_fallback_close_delegates_both():
    primary = FakeProvider(["ok"])
    fallback = FakeProvider(["ok"])
    await FallbackAIProvider(primary, fallback).close()
    assert primary.closed and fallback.closed


# --- InMemoryRateLimiter ----------------------------------------------------

def test_rate_limiter_allows_up_to_limit():
    rl = InMemoryRateLimiter()
    assert all(rl.try_acquire("user:1", limit=3) for _ in range(3))
    assert rl.try_acquire("user:1", limit=3) is False


def test_rate_limiter_keys_isolated():
    rl = InMemoryRateLimiter()
    for _ in range(3):
        rl.try_acquire("a", limit=3)
    # другой ключ не затронут
    assert rl.try_acquire("b", limit=3) is True


def test_rate_limiter_window_slides(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr("src.infrastructure.ai.rate_limiter.time.monotonic", lambda: clock["t"])
    rl = InMemoryRateLimiter()
    assert rl.try_acquire("k", limit=1, window_seconds=100)
    assert rl.try_acquire("k", limit=1, window_seconds=100) is False
    clock["t"] += 101  # старое попадание выпало из окна
    assert rl.try_acquire("k", limit=1, window_seconds=100) is True
