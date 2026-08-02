"""Use-case апелляций против реальной БД (uow_factory-фикстура): создание с
антиспамом и подтяжкой причины, разбор (идемпотентность), список открытых."""

from datetime import UTC, datetime

from src.application.appeals.use_cases import (
    CreateAppealUseCase,
    ListPendingAppealsUseCase,
    ResolveAppealUseCase,
)
from src.domain.moderation.entities import ModCase

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


async def _seed_case(uow_factory, guild_id, user_id, action, reason):
    async with uow_factory() as uow:
        await uow.mod_cases.add(
            ModCase(
                guild_id=guild_id,
                user_id=user_id,
                moderator_id=1,
                action=action,
                reason=reason,
                created_at=NOW,
            )
        )
        await uow.commit()


async def test_create_pulls_reason_from_last_case(uow_factory):
    await _seed_case(uow_factory, 10, 5, "ban", "спам ссылками")
    result = await CreateAppealUseCase(uow_factory).execute(10, 5, "ban", "это друг скинул", NOW)
    assert result.ok
    assert result.appeal.id is not None
    assert result.appeal.original_reason == "спам ссылками"
    assert result.appeal.status == "pending"


async def test_create_accepts_kick(uow_factory):
    await _seed_case(uow_factory, 10, 5, "kick", "нарушение правил")
    result = await CreateAppealUseCase(uow_factory).execute(10, 5, "kick", "я исправлюсь", NOW)
    assert result.ok
    assert result.appeal.action == "kick"
    assert result.appeal.original_reason == "нарушение правил"


async def test_create_rejects_duplicate(uow_factory):
    create = CreateAppealUseCase(uow_factory)
    assert (await create.execute(10, 5, "mute", "первый", NOW)).ok
    second = await create.execute(10, 5, "ban", "второй", NOW)
    assert not second.ok and second.error == "duplicate"


async def test_create_rejects_bad_action(uow_factory):
    r = await CreateAppealUseCase(uow_factory).execute(10, 5, "warn", "текст", NOW)
    assert not r.ok and r.error == "bad_action"


async def test_create_rejects_empty_text(uow_factory):
    r = await CreateAppealUseCase(uow_factory).execute(10, 5, "ban", "   ", NOW)
    assert not r.ok and r.error == "empty"


async def test_resolve_approve_then_already(uow_factory):
    created = await CreateAppealUseCase(uow_factory).execute(10, 5, "ban", "текст", NOW)
    resolve = ResolveAppealUseCase(uow_factory)
    first = await resolve.execute(created.appeal.id, True, 99, NOW)
    assert first.ok and first.approved
    assert first.appeal.status == "approved" and first.appeal.resolver_id == 99
    again = await resolve.execute(created.appeal.id, False, 99, NOW)
    assert not again.ok and again.error == "already"


async def test_resolve_not_found(uow_factory):
    r = await ResolveAppealUseCase(uow_factory).execute(999, True, 1, NOW)
    assert not r.ok and r.error == "not_found"


async def test_list_pending_excludes_resolved(uow_factory):
    create = CreateAppealUseCase(uow_factory)
    a1 = await create.execute(10, 5, "ban", "a", NOW)
    await create.execute(10, 6, "mute", "b", NOW)
    await ResolveAppealUseCase(uow_factory).execute(a1.appeal.id, False, 1, NOW)
    pending = await ListPendingAppealsUseCase(uow_factory).execute(10)
    assert [p.user_id for p in pending] == [6]


async def test_reappeal_allowed_after_resolution(uow_factory):
    create = CreateAppealUseCase(uow_factory)
    a1 = await create.execute(10, 5, "ban", "первый", NOW)
    await ResolveAppealUseCase(uow_factory).execute(a1.appeal.id, False, 1, NOW)
    # активной больше нет — можно подать снова
    assert (await create.execute(10, 5, "ban", "вторая попытка", NOW)).ok
