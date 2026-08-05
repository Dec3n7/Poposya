from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.moderation.entities import (
    CASE_WARN,
    CASE_WARN_MUTE,
    CASE_WARN_TEMPBAN,
    ESCALATION_CASES,
    ModCase,
    TempBan,
    Warn,
)

UowFactory = Callable[[], IUnitOfWork]

# жёсткий предел Discord на timeout — 28 суток; эскалированный мут туда клампим
_TIMEOUT_MAX_MINUTES = 40320


@dataclass(frozen=True)
class WarnResult:
    count: int
    threshold: int
    action: str  # "none" | "mute" | "tempban" — что кога должна применить
    minutes: int  # длительность мута/tempban (0 для "none")
    offense: int  # какое по счёту достижение порога (1..); 0 если порог не достигнут

    @property
    def mute_triggered(self) -> bool:
        """Порог достигнут — варны сброшены, к участнику применяется наказание."""
        return self.action != "none"


class WarnUserUseCase:
    """Варн с затуханием и лестницей эскалации. Каждый варн пишется в единый
    журнал кейсов; при достижении порога — авто-наказание (мут, при рецидиве —
    длиннее, затем tempban) и запись соответствующего кейса в той же транзакции."""

    def __init__(
        self,
        uow_factory: UowFactory,
        threshold: int,
        *,
        mute_minutes: int = 120,
        ban_minutes: int = 1440,
        expire_days: int = 0,
        escalation: bool = False,
        settings_provider=None,
    ):
        self._uow_factory = uow_factory
        self._threshold = threshold
        self._mute_minutes = mute_minutes
        self._ban_minutes = ban_minutes
        self._expire_days = expire_days
        self._escalation = escalation
        self._settings = settings_provider

    def _cfg(self, guild_id: int, key: str, default):
        if self._settings is not None:
            return self._settings.get(guild_id, key, default)
        return default

    async def execute(
        self, user_id: int, guild_id: int, moderator_id: int, reason: str, now: datetime
    ) -> WarnResult:
        threshold = self._cfg(guild_id, "warn_threshold", self._threshold)
        expire_days = self._cfg(guild_id, "warn_expire_days", self._expire_days)
        escalation = self._cfg(guild_id, "warn_escalation", self._escalation)
        mute_minutes = self._cfg(guild_id, "warn_mute_minutes", self._mute_minutes)
        ban_minutes = self._cfg(guild_id, "warn_ban_minutes", self._ban_minutes)
        since = now - timedelta(days=expire_days) if expire_days and expire_days > 0 else None

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
            await uow.mod_cases.add(
                ModCase(
                    guild_id=guild_id,
                    user_id=user_id,
                    moderator_id=moderator_id,
                    action=CASE_WARN,
                    reason=reason,
                    created_at=now,
                )
            )
            count = await uow.warns.count(user_id, guild_id, since=since)
            if count < threshold:
                await uow.commit()
                return WarnResult(
                    count=count, threshold=threshold, action="none", minutes=0, offense=0
                )

            # порог достигнут: определяем строгость по числу прошлых авто-наказаний
            prior = await uow.mod_cases.count_for_user(guild_id, user_id, ESCALATION_CASES)
            offense = prior + 1
            if escalation and offense >= 3:
                action, minutes, case = "tempban", ban_minutes, CASE_WARN_TEMPBAN
            elif escalation and offense == 2:
                action = "mute"
                minutes = min(mute_minutes * 3, _TIMEOUT_MAX_MINUTES)
                case = CASE_WARN_MUTE
            else:
                action, minutes, case = "mute", mute_minutes, CASE_WARN_MUTE

            await uow.warns.clear(user_id, guild_id)  # после наказания счёт с нуля
            await uow.mod_cases.add(
                ModCase(
                    guild_id=guild_id,
                    user_id=user_id,
                    moderator_id=moderator_id,
                    action=case,
                    reason=reason,
                    duration_minutes=minutes,
                    created_at=now,
                )
            )
            await uow.commit()
            return WarnResult(
                count=count, threshold=threshold, action=action, minutes=minutes, offense=offense
            )


class GetWarnsUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int) -> list[Warn]:
        async with self._uow_factory() as uow:
            return await uow.warns.list(user_id, guild_id)


class ListGuildWarnsUseCase:
    """Кто на сервере сейчас с варнами: (user_id, число, последний_варн)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int) -> Sequence[tuple[int, int, datetime]]:
        async with self._uow_factory() as uow:
            return await uow.warns.list_guild_counts(guild_id)


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


class LogModCaseUseCase:
    """Запись одного действия модерации в единый журнал. Зовут и ког (слеш-
    команды), и командный мост (действия панели) — единый источник истории."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, case: ModCase) -> ModCase:
        async with self._uow_factory() as uow:
            saved = await uow.mod_cases.add(case)
            await uow.commit()
            return saved


class GetUserHistoryUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, user_id: int, limit: int = 50) -> list[ModCase]:
        async with self._uow_factory() as uow:
            return await uow.mod_cases.list_for_user(guild_id, user_id, limit)
