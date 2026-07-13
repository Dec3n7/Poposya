from abc import ABC, abstractmethod


class IRateLimiter(ABC):
    """Per-user троттлинг AI-реплик (ТЗ 8.4): защищает очередь от
    монополизации одним пользователем. Лимит зависит от уровня отношений."""

    @abstractmethod
    def try_acquire(self, key: str, limit: int, window_seconds: int = 3600) -> bool:
        """True — квота есть (и потрачена), False — лимит исчерпан."""
