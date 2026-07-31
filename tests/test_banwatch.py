"""Use cases модуля banwatch на реальном UoW: запись/снятие банов, синхронизация
сервера, кросс-серверный отчёт (исключение текущего сервера) и отбор отмеченных."""

from datetime import UTC, datetime

from src.application.banwatch.use_cases import (
    CheckUserUseCase,
    FlaggedCandidatesUseCase,
    RecordBanUseCase,
    RemoveBanUseCase,
    SyncGuildBansUseCase,
)
from src.domain.banwatch.entities import ServerBan

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def ban(user_id=100, guild_id=1, **over):
    base = dict(user_id=user_id, guild_id=guild_id, guild_name=f"G{guild_id}", reason="spam")
    base.update(over)
    return ServerBan(**base)


async def test_record_and_check_excludes_current_guild(uow_factory):
    rec = RecordBanUseCase(uow_factory)
    await rec.execute(ban(guild_id=1, reason="here"))
    await rec.execute(ban(guild_id=2, reason="raid", banned_at=NOW))
    await rec.execute(ban(guild_id=3, reason="spam"))

    # смотрим с сервера 1 → он исключён, видим только 2 и 3
    report = await CheckUserUseCase(uow_factory).execute(100, exclude_guild_id=1)
    assert report.count == 2
    assert {r.guild_id for r in report.records} == {2, 3}
    assert all(r.guild_id != 1 for r in report.records)


async def test_check_user_none_when_clean(uow_factory):
    report = await CheckUserUseCase(uow_factory).execute(999, exclude_guild_id=1)
    assert report.count == 0
    assert report.records == []


async def test_remove(uow_factory):
    rec = RecordBanUseCase(uow_factory)
    await rec.execute(ban(guild_id=1))
    await rec.execute(ban(guild_id=2))
    await RemoveBanUseCase(uow_factory).execute(1, 100)
    report = await CheckUserUseCase(uow_factory).execute(100, exclude_guild_id=99)
    assert {r.guild_id for r in report.records} == {2}


async def test_sync_guild(uow_factory):
    rec = RecordBanUseCase(uow_factory)
    await rec.execute(ban(user_id=100, guild_id=1))
    # бэкфилл заменяет баны сервера 1: теперь там только 300
    await SyncGuildBansUseCase(uow_factory).execute(1, [ban(user_id=300, guild_id=1)])
    assert (await CheckUserUseCase(uow_factory).execute(100, 99)).count == 0
    assert (await CheckUserUseCase(uow_factory).execute(300, 99)).count == 1


async def test_flagged_candidates_use_case(uow_factory):
    rec = RecordBanUseCase(uow_factory)
    for g in (1, 2, 3, 4):
        await rec.execute(ban(user_id=100, guild_id=g))
    for g in (2, 3):
        await rec.execute(ban(user_id=200, guild_id=g))

    flagged = await FlaggedCandidatesUseCase(uow_factory).execute(guild_id=1, threshold=3)
    assert [(f.user_id, f.count) for f in flagged] == [(100, 3)]
