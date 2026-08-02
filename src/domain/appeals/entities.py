"""Апелляция на наказание: наказанный обжалует бан/темпбан/мут/кик кнопкой в ЛС,
модератор принимает (наказание снимается) или отклоняет. Одна активная апелляция
на участника — защита от спама."""

from dataclasses import dataclass
from datetime import datetime

# что обжалуют (оно же решает, как снимать при одобрении). Кик снять нельзя —
# человек уже вне сервера; одобрение кика лишь уведомляет его (мод сам зовёт назад).
ACTION_BAN = "ban"
ACTION_TEMPBAN = "tempban"
ACTION_MUTE = "mute"
ACTION_KICK = "kick"
APPEALABLE = (ACTION_BAN, ACTION_TEMPBAN, ACTION_MUTE, ACTION_KICK)

# статусы
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"


@dataclass
class Appeal:
    guild_id: int
    user_id: int
    action: str  # один из APPEALABLE
    text: str  # текст апелляции от участника
    created_at: datetime
    original_reason: str = ""  # причина наказания (для контекста модератору)
    id: int | None = None
    status: str = STATUS_PENDING
    review_message_id: int = 0  # сообщение с кнопками в канале апелляций
    resolved_at: datetime | None = None
    resolver_id: int = 0  # кто принял/отклонил

    @property
    def is_pending(self) -> bool:
        return self.status == STATUS_PENDING
