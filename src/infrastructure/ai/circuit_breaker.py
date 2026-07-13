import logging
import time

from src.application.interfaces.ai_provider import ChatMessage, IAIProvider
from src.domain.ai_chat.exceptions import AIProviderError

logger = logging.getLogger(__name__)


class CircuitBreakerAIProvider(IAIProvider):
    """Circuit Breaker по контракту ТЗ 8.3: внешняя обёртка всей цепочки.
    Счётчик сбоев сбрасывается при ЛЮБОМ успехе (иначе редкие несвязанные
    сбои копятся неделями и ложно размыкают цепь); неудачная проба в
    HALF_OPEN размыкает сразу. Пока цепь открыта, запросы отклоняются
    мгновенно — без таймаутов на каждое сообщение."""

    def __init__(self, inner: IAIProvider, failure_threshold: int = 5, timeout: int = 60):
        self._inner = inner
        self._failure_threshold = failure_threshold
        self._timeout = timeout
        self._failure_count = 0
        self._state = "CLOSED"  # CLOSED | OPEN | HALF_OPEN
        self._last_failure_time: float | None = None

    def _before_call(self) -> None:
        if self._state != "OPEN":
            return
        if time.monotonic() - (self._last_failure_time or 0.0) > self._timeout:
            self._state = "HALF_OPEN"
            logger.info("Circuit breaker: HALF_OPEN (пробный запрос)")
        else:
            raise AIProviderError("AI временно недоступен (circuit breaker открыт)")

    def _on_success(self) -> None:
        if self._state != "CLOSED":
            logger.info("Circuit breaker: CLOSED (провайдер ожил)")
        self._state = "CLOSED"
        self._failure_count = 0

    def _on_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._state == "HALF_OPEN" or self._failure_count >= self._failure_threshold:
            if self._state != "OPEN":
                logger.warning(
                    "Circuit breaker: OPEN на %d c (сбоев подряд: %d)",
                    self._timeout,
                    self._failure_count,
                )
            self._state = "OPEN"

    async def generate(self, system_prompt: str, messages: list[ChatMessage]) -> str:
        self._before_call()
        try:
            result = await self._inner.generate(system_prompt, messages)
        except Exception:
            self._on_failure()
            raise
        self._on_success()
        return result

    async def close(self) -> None:
        await self._inner.close()
