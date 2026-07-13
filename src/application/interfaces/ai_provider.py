from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str


class IAIProvider(ABC):
    """Порт генерации текста. Реализации — в infrastructure/ai."""

    @abstractmethod
    async def generate(self, system_prompt: str, messages: list[ChatMessage]) -> str: ...

    @abstractmethod
    async def close(self) -> None: ...
