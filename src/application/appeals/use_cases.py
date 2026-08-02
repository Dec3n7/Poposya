"""Апелляции: создать (с антиспамом), разобрать (принять/отклонить), список
открытых для панели. Снятие самого наказания (разбан/анмут) делает ког — здесь
только состояние апелляции в БД."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.appeals.entities import (
    APPEALABLE,
    STATUS_APPROVED,
    STATUS_REJECTED,
    Appeal,
)

UowFactory = Callable[[], IUnitOfWork]

_TEXT_MAX = 1000  # предел текста апелляции


@dataclass(frozen=True)
class AppealResult:
    ok: bool
    appeal: Appeal | None = None
    error: str = ""  # "bad_action" | "empty" | "duplicate"


class CreateAppealUseCase:
    """Создать апелляцию. Антиспам: одна активная (pending) на участника —
    повторная попытка при открытой возвращает error="duplicate"."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self, guild_id: int, user_id: int, action: str, text: str, now: datetime
    ) -> AppealResult:
        action = (action or "").strip()
        if action not in APPEALABLE:
            return AppealResult(False, error="bad_action")
        text = (text or "").strip()[:_TEXT_MAX]
        if not text:
            return AppealResult(False, error="empty")
        async with self._uow_factory() as uow:
            if await uow.appeals.get_pending(guild_id, user_id) is not None:
                return AppealResult(False, error="duplicate")
            # причина наказания — из последнего кейса этого типа (лучший эффорт)
            reason = ""
            for case in await uow.mod_cases.list_for_user(guild_id, user_id, limit=20):
                if case.action == action:
                    reason = case.reason
                    break
            appeal = await uow.appeals.add(
                Appeal(
                    guild_id=guild_id,
                    user_id=user_id,
                    action=action,
                    text=text,
                    original_reason=reason,
                    created_at=now,
                )
            )
            await uow.commit()
        return AppealResult(True, appeal=appeal)


@dataclass(frozen=True)
class ResolveResult:
    ok: bool
    appeal: Appeal | None = None
    approved: bool = False
    error: str = ""  # "not_found" | "already"


class ResolveAppealUseCase:
    """Принять/отклонить. Идемпотентно: уже разобранную второй раз не трогаем
    (кнопка в канале и панель могут прийти оба — вторая получит error="already")."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self, appeal_id: int, approve: bool, resolver_id: int, now: datetime
    ) -> ResolveResult:
        async with self._uow_factory() as uow:
            appeal = await uow.appeals.get(appeal_id)
            if appeal is None:
                return ResolveResult(False, error="not_found")
            if not appeal.is_pending:
                return ResolveResult(False, appeal=appeal, error="already")
            appeal.status = STATUS_APPROVED if approve else STATUS_REJECTED
            appeal.resolver_id = resolver_id
            appeal.resolved_at = now
            await uow.appeals.save(appeal)
            await uow.commit()
        return ResolveResult(True, appeal=appeal, approved=approve)


class ListPendingAppealsUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int) -> list[Appeal]:
        async with self._uow_factory() as uow:
            return await uow.appeals.list_pending(guild_id)
