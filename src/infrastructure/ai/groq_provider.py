import logging

import aiohttp

from src.application.interfaces.ai_provider import ChatMessage, IAIProvider
from src.domain.ai_chat.exceptions import AIProviderError

logger = logging.getLogger(__name__)

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqAIProvider(IAIProvider):
    """Groq — OpenAI-совместимый API; отдельный SDK не нужен, хватает aiohttp
    (он уже в зависимостях discord.py)."""

    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        temperature: float = 0.8,
        max_tokens: int = 400,
        timeout_seconds: int = 60,
    ):
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def generate(self, system_prompt: str, messages: list[ChatMessage]) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *({"role": m.role, "content": m.content} for m in messages),
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with self._get_session().post(_GROQ_URL, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    retry_after = None
                    if resp.status == 429:
                        try:
                            retry_after = float(resp.headers.get("retry-after", ""))
                        except ValueError:
                            pass
                    raise AIProviderError(
                        f"Groq HTTP {resp.status}: {body[:300]}",
                        retryable=resp.status == 429 or resp.status >= 500,
                        retry_after=retry_after,
                    )
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            # TimeoutError — не подкласс ClientError: без этой ветки таймаут
            # не превращался бы в retryable-ошибку и не ретраился
            raise AIProviderError(f"Groq недоступен: {exc}", retryable=True) from exc

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError(f"Неожиданный ответ Groq: {str(data)[:300]}") from exc

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
