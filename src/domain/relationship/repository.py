from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.relationship.entities import RelationshipProfile, SecretCode, SecretRoom


class ISecretRoomRepository(ABC):
    """Ключи и комнаты хранятся в БД: рестарт не гасит таймер комнаты."""

    @abstractmethod
    async def get_code(self, user_id: int, guild_id: int) -> SecretCode | None: ...

    @abstractmethod
    async def save_code(self, code: SecretCode) -> None: ...

    @abstractmethod
    async def get_active_room(self, guild_id: int, now: datetime) -> SecretRoom | None: ...

    @abstractmethod
    async def add_room(self, room: SecretRoom) -> None: ...

    @abstractmethod
    async def pop_expired_rooms(self, now: datetime) -> list[SecretRoom]: ...


class IRelationshipRepository(ABC):
    @abstractmethod
    async def get(self, user_id: int, guild_id: int) -> RelationshipProfile | None: ...

    @abstractmethod
    async def get_or_create(self, user_id: int, guild_id: int) -> RelationshipProfile: ...

    @abstractmethod
    async def get_exclusive_holder(self, guild_id: int) -> RelationshipProfile | None: ...

    @abstractmethod
    async def find_birthdays(self, month: int, day: int) -> list[RelationshipProfile]:
        """Профили с днём рождения в указанную дату (по всем гильдиям)."""

    @abstractmethod
    async def top_by_points(self, guild_id: int, limit: int) -> list[RelationshipProfile]: ...

    @abstractmethod
    async def list_decayable(
        self, inactive_before: datetime, decayed_before: datetime
    ) -> list[RelationshipProfile]:
        """Профили под угасание: молчат дольше inactive_before, очки > 0,
        не заморожены, последнее списание раньше decayed_before."""

    @abstractmethod
    async def save(self, profile: RelationshipProfile) -> None: ...


class IDialogSummaryRepository(ABC):
    """Память о прошлых разговорах: короткие резюме, последние N на человека."""

    @abstractmethod
    async def add(self, guild_id: int, user_id: int, summary: str, at: datetime, keep: int) -> None:
        """Сохраняет резюме и удаляет старые сверх keep."""

    @abstractmethod
    async def last(self, guild_id: int, user_id: int, limit: int) -> list[str]: ...
