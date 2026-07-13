import asyncio
import logging
import random

from src.application.interfaces.ai_provider import ChatMessage, IAIProvider
from src.domain.ai_chat.exceptions import AIProviderError

logger = logging.getLogger(__name__)

_MAX_SINGLE_DELAY = 8.0  # не держим Discord-интеракцию дольше разумного


class ResilientAIProvider(IAIProvider):
    """Retry с экспоненциальным backoff (ТЗ 8.2). Повторяет только
    временные сбои (429/5xx/сеть); уважает Retry-After провайдера."""

    def __init__(self, inner: IAIProvider, attempts: int = 3, base_delay: float = 1.0):
        self._inner = inner
        self._attempts = max(1, attempts)
        self._base_delay = base_delay

    async def generate(self, system_prompt: str, messages: list[ChatMessage]) -> str:
        last_error: AIProviderError | None = None
        for attempt in range(self._attempts):
            try:
                return await self._inner.generate(system_prompt, messages)
            except AIProviderError as exc:
                last_error = exc
                if not exc.retryable or attempt == self._attempts - 1:
                    raise
                delay = self._base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                if exc.retry_after is not None:
                    delay = max(delay, exc.retry_after)
                delay = min(delay, _MAX_SINGLE_DELAY)
                logger.info(
                    "AI-запрос не удался, повтор через %.1f c (попытка %d/%d)",
                    delay, attempt + 2, self._attempts,
                )
                await asyncio.sleep(delay)
        raise last_error  # недостижимо, но успокаивает типизацию

    async def close(self) -> None:
        await self._inner.close()


class FallbackAIProvider(IAIProvider):
    """Если основная модель не ответила (после ретраев) — тот же запрос
    уходит в резервную. Реплика проще, но она есть."""

    def __init__(self, primary: IAIProvider, fallback: IAIProvider):
        self._primary = primary
        self._fallback = fallback

    async def generate(self, system_prompt: str, messages: list[ChatMessage]) -> str:
        try:
            return await self._primary.generate(system_prompt, messages)
        except AIProviderError as exc:
            logger.warning("Основная модель недоступна (%s) — фолбэк", exc)
            return await self._fallback.generate(system_prompt, messages)

    async def close(self) -> None:
        await self._primary.close()
        await self._fallback.close()
