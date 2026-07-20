from collections.abc import Callable
from datetime import UTC, datetime

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.audit.entities import AuditEntry

UowFactory = Callable[[], IUnitOfWork]


class AppendAuditUseCase:
    """Записывает одно действие панели в журнал (отдельной транзакцией)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, entry: AuditEntry) -> None:
        if entry.created_at is None:
            entry.created_at = datetime.now(UTC)
        async with self._uow_factory() as uow:
            await uow.audit.add(entry)
            await uow.commit()


class ListAuditUseCase:
    """Последние записи журнала сервера, новые → старые."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, limit: int = 100) -> list[AuditEntry]:
        async with self._uow_factory() as uow:
            return await uow.audit.list_for_guild(guild_id, limit)
