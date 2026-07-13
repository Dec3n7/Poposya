class AIChatError(Exception):
    """Базовое исключение AI-фичи."""


class AIProviderError(AIChatError):
    """Провайдер не смог сгенерировать ответ.

    retryable — временный сбой (429, 5xx, сеть): имеет смысл повторить;
    retry_after — сколько секунд просил подождать сам провайдер (429)."""

    def __init__(self, message: str, retryable: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
