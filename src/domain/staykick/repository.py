from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.staykick.entities import PendingKick


class IPendingKickRepository(ABC):
    @abstractmethod
    async def schedule(self, kick: PendingKick) -> None:
        """Ставит/заменяет авто-кик для (guild_id, user_id)."""

    @abstractmethod
    async def cancel(self, guild_id: int, user_id: int) -> bool:
        """Снимает запланированный кик. False — его и не было."""

    @abstractmethod
    async def pop_due_kicks(self, now: datetime) -> list[PendingKick]:
        """Возвращает и удаляет кики, чей срок настал."""

    @abstractmethod
    async def due_reminders(self, now: datetime) -> list[PendingKick]:
        """Кому пора напомнить (remind_at настал, ещё не напоминали, кик впереди).
        Помечает как напомненные."""
