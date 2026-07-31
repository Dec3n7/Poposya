from collections.abc import Callable
from datetime import UTC, datetime

from src.application.banwatch.dto import BanRecordDTO, CrossBanReport, FlaggedUser
from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.banwatch.entities import ServerBan

UowFactory = Callable[[], IUnitOfWork]

_OLDEST = datetime(1970, 1, 1, tzinfo=UTC)


class RecordBanUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, ban: ServerBan) -> None:
        async with self._uow_factory() as uow:
            await uow.server_bans.upsert(ban)
            await uow.commit()


class RemoveBanUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, user_id: int) -> None:
        async with self._uow_factory() as uow:
            await uow.server_bans.remove(guild_id, user_id)
            await uow.commit()


class SyncGuildBansUseCase:
    """Бэкфилл: заменить все баны сервера текущим списком из Discord."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, bans: list[ServerBan]) -> None:
        async with self._uow_factory() as uow:
            await uow.server_bans.replace_guild(guild_id, bans)
            await uow.commit()


class CheckUserUseCase:
    """Кросс-серверная бан-история пользователя по ДРУГИМ серверам (не текущему)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, exclude_guild_id: int) -> CrossBanReport:
        async with self._uow_factory() as uow:
            bans = await uow.server_bans.list_for_user(user_id)
        records = [
            BanRecordDTO(b.guild_id, b.guild_name, b.reason, b.banned_at)
            for b in bans
            if b.guild_id != exclude_guild_id
        ]
        records.sort(key=lambda r: r.banned_at or _OLDEST, reverse=True)
        return CrossBanReport(user_id=user_id, count=len(records), records=records)


class FlaggedCandidatesUseCase:
    """Пользователи, забанённые на >= threshold серверах, кроме текущего.
    Множество маленькое — членство на сервере проверяет уже вызывающий."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, threshold: int) -> list[FlaggedUser]:
        async with self._uow_factory() as uow:
            rows = await uow.server_bans.flagged_candidates(guild_id, threshold)
        return [FlaggedUser(user_id, count) for user_id, count in rows]
