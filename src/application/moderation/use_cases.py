from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.moderation.entities import TempBan, Warn

UowFactory = Callable[[], IUnitOfWork]


@dataclass(frozen=True)
class WarnResult:
    count: int
    threshold: int
    mute_triggered: bool  # порог достигнут: варны сброшены, кога мутит


class WarnUserUseCase:
    def __init__(self, uow_factory: UowFactory, threshold: int, settings_provider=None):
        self._uow_factory = uow_factory
        self._threshold = threshold
        self._settings = settings_provider

    async def execute(
        self, user_id: int, guild_id: int, moderator_id: int, reason: str, now: datetime
    ) -> WarnResult:
        threshold = (
            self._settings.get(guild_id, "warn_threshold", self._threshold)
            if self._settings is not None
            else self._threshold
        )
        async with self._uow_factory() as uow:
            await uow.warns.add(
                Warn(
                    guild_id=guild_id,
                    user_id=user_id,
                    moderator_id=moderator_id,
                    reason=reason,
                    created_at=now,
                )
            )
            count = await uow.warns.count(user_id, guild_id)
            mute = count >= threshold
            if mute:
                # после мута счёт начинается заново
                await uow.warns.clear(user_id, guild_id)
            await uow.commit()
            return WarnResult(count=count, threshold=threshold, mute_triggered=mute)


class GetWarnsUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int) -> list[Warn]:
        async with self._uow_factory() as uow:
            return await uow.warns.list(user_id, guild_id)


class ClearWarnsUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int) -> int:
        """Возвращает, сколько варнов было сброшено."""
        async with self._uow_factory() as uow:
            count = await uow.warns.count(user_id, guild_id)
            await uow.warns.clear(user_id, guild_id)
            await uow.commit()
            return count


class TempBanUserUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self,
        user_id: int,
        guild_id: int,
        moderator_id: int,
        reason: str,
        minutes: int,
        now: datetime,
    ) -> datetime:
        expires_at = now + timedelta(minutes=minutes)
        async with self._uow_factory() as uow:
            # повторный бан заменяет предыдущую запись
            await uow.temp_bans.remove(user_id, guild_id)
            await uow.temp_bans.add(
                TempBan(
                    guild_id=guild_id,
                    user_id=user_id,
                    moderator_id=moderator_id,
                    reason=reason,
                    expires_at=expires_at,
                )
            )
            await uow.commit()
            return expires_at


class RemoveTempBanUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int) -> bool:
        async with self._uow_factory() as uow:
            removed = await uow.temp_bans.remove(user_id, guild_id)
            await uow.commit()
            return removed


class ListTempBansUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, now: datetime) -> list[TempBan]:
        async with self._uow_factory() as uow:
            return await uow.temp_bans.list_active(guild_id, now)


class PopExpiredBansUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, now: datetime) -> list[TempBan]:
        async with self._uow_factory() as uow:
            expired = await uow.temp_bans.pop_expired(now)
            await uow.commit()
            return expired
