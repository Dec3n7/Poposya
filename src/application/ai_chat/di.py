from dataclasses import dataclass

from src.application.ai_chat.service import ChatService


@dataclass(frozen=True)
class AIChatContainer:
    chat_service: ChatService | None  # None, если GROQ_API_KEY не задан
