from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.activity.entities import Reminder


class IMemberActivityRepository(ABC):
    """Последнее сообщение участника в гильдии — для детекции возвращений."""

    @abstractmethod
    async def get_last_message(self, user_id: int, guild_id: int) -> datetime | None: ...

    @abstractmethod
    async def set_last_message(self, user_id: int, guild_id: int, at: datetime) -> None: ...


class IReminderRepository(ABC):
    """Напоминания хранятся в БД и переживают рестарт."""

    @abstractmethod
    async def add(self, reminder: Reminder) -> None: ...

    @abstractmethod
    async def pop_due(self, now: datetime) -> list[Reminder]:
        """Удаляет и возвращает напоминания, чей срок наступил."""


class IVoiceProgressRepository(ABC):
    """Минуты в войсе: недосиженный до начисления остаток + накопительный
    итог для профиля. Переживают рестарт бота."""

    @abstractmethod
    async def load_all(self) -> dict[tuple[int, int], float]:
        """(guild_id, user_id) -> накопленные к начислению минуты."""

    @abstractmethod
    async def save_many(
        self, progress: dict[tuple[int, int], float], accrued_minutes: float = 0.0
    ) -> None:
        """Upsert счётчиков; accrued_minutes добавляется каждому в total."""

    @abstractmethod
    async def total_minutes(self, guild_id: int, user_id: int) -> float:
        """Суммарное время в войсе за всю историю."""


class IAlbumRepository(ABC):
    """Дедупликация «Альбома Попоси»: сообщение публикуется один раз."""

    @abstractmethod
    async def was_posted(self, guild_id: int, message_id: int) -> bool: ...

    @abstractmethod
    async def mark_posted(self, guild_id: int, message_id: int, at: datetime) -> None: ...
