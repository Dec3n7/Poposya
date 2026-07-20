from abc import ABC, abstractmethod

from src.domain.audit.entities import AuditEntry


class IAuditRepository(ABC):
    """Журнал действий панели. Только запись (из write-эндпоинтов) и чтение
    последних записей сервера — правок/удаления нет (аудит неизменяем)."""

    @abstractmethod
    async def add(self, entry: AuditEntry) -> None: ...

    @abstractmethod
    async def list_for_guild(self, guild_id: int, limit: int) -> list[AuditEntry]:
        """Последние записи сервера, новые → старые."""
