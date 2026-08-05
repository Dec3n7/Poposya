"""Единый журнал кейсов, лестница эскалации и затухание варнов — поверх
реального UoW+SQLite (та же фикстура uow_factory, что и остальные use-case тесты)."""

from datetime import UTC, datetime, timedelta

from src.application.moderation.use_cases import (
    GetUserHistoryUseCase,
    LogModCaseUseCase,
    WarnUserUseCase,
)
from src.domain.moderation.entities import (
    CASE_WARN,
    CASE_WARN_MUTE,
    CASE_WARN_TEMPBAN,
    ModCase,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


async def _cases(uow_factory, guild_id, user_id):
    async with uow_factory() as uow:
        return await uow.mod_cases.list_for_user(guild_id, user_id, limit=100)


async def test_warn_writes_case_to_journal(uow_factory):
    warn = WarnUserUseCase(uow_factory, threshold=3)
    await warn.execute(1, 10, moderator_id=99, reason="спам", now=NOW)
    cases = await _cases(uow_factory, 10, 1)
    assert [c.action for c in cases] == [CASE_WARN]
    assert cases[0].reason == "спам" and cases[0].moderator_id == 99


async def test_warn_mute_logs_escalation_case(uow_factory):
    warn = WarnUserUseCase(uow_factory, threshold=1, mute_minutes=15)
    r = await warn.execute(1, 10, moderator_id=99, reason="перебор", now=NOW)
    assert r.action == "mute" and r.minutes == 15 and r.mute_triggered
    actions = [c.action for c in await _cases(uow_factory, 10, 1)]
    assert CASE_WARN in actions and CASE_WARN_MUTE in actions


async def test_escalation_ladder_mute_then_longer_then_tempban(uow_factory):
    warn = WarnUserUseCase(
        uow_factory, threshold=1, mute_minutes=10, ban_minutes=100, escalation=True
    )
    r1 = await warn.execute(1, 10, moderator_id=99, reason="a", now=NOW)
    r2 = await warn.execute(1, 10, moderator_id=99, reason="b", now=NOW)
    r3 = await warn.execute(1, 10, moderator_id=99, reason="c", now=NOW)
    assert (r1.action, r1.minutes) == ("mute", 10)  # 1-е достижение — базовый мут
    assert (r2.action, r2.minutes) == ("mute", 30)  # 2-е — мут ×3
    assert r3.action == "tempban" and r3.minutes == 100  # 3-е — tempban
    # в журнале — два мута по варнам и один бан по варнам
    actions = [c.action for c in await _cases(uow_factory, 10, 1)]
    assert actions.count(CASE_WARN_MUTE) == 2
    assert actions.count(CASE_WARN_TEMPBAN) == 1


async def test_no_escalation_keeps_flat_mute(uow_factory):
    warn = WarnUserUseCase(uow_factory, threshold=1, mute_minutes=10, escalation=False)
    r1 = await warn.execute(1, 10, moderator_id=99, reason="a", now=NOW)
    r2 = await warn.execute(1, 10, moderator_id=99, reason="b", now=NOW)
    assert (r1.minutes, r2.minutes) == (10, 10)  # без эскалации срок не растёт


async def test_warn_decay_old_warns_do_not_count(uow_factory):
    warn = WarnUserUseCase(uow_factory, threshold=2, expire_days=30)
    old = NOW - timedelta(days=40)
    r_old = await warn.execute(1, 10, moderator_id=99, reason="old", now=old)
    r_new = await warn.execute(1, 10, moderator_id=99, reason="new", now=NOW)
    # старый варн вне окна 30 дней — второй свежий варн ещё НЕ доводит до порога
    assert r_old.action == "none"
    assert r_new.action == "none"
    r_new2 = await warn.execute(1, 10, moderator_id=99, reason="new2", now=NOW)
    assert r_new2.mute_triggered  # два свежих варна -> порог


async def test_log_case_and_history_newest_first(uow_factory):
    log = LogModCaseUseCase(uow_factory)
    await log.execute(
        ModCase(guild_id=10, user_id=1, moderator_id=99, action="mute", reason="1", created_at=NOW)
    )
    await log.execute(
        ModCase(
            guild_id=10,
            user_id=1,
            moderator_id=99,
            action="kick",
            reason="2",
            created_at=NOW + timedelta(minutes=1),
        )
    )
    history = await GetUserHistoryUseCase(uow_factory).execute(10, 1)
    assert [c.action for c in history] == ["kick", "mute"]  # свежие сверху
    assert history[0].id is not None


async def test_count_for_user_filters_by_action(uow_factory):
    log = LogModCaseUseCase(uow_factory)
    for action in ("mute", "warn_mute", "warn_mute", "kick"):
        await log.execute(
            ModCase(
                guild_id=10, user_id=1, moderator_id=0, action=action, reason="", created_at=NOW
            )
        )
    async with uow_factory() as uow:
        n = await uow.mod_cases.count_for_user(10, 1, ("warn_mute", "warn_tempban"))
    assert n == 2


async def test_history_isolated_per_guild(uow_factory):
    log = LogModCaseUseCase(uow_factory)
    await log.execute(
        ModCase(guild_id=10, user_id=1, moderator_id=0, action="mute", reason="", created_at=NOW)
    )
    await log.execute(
        ModCase(guild_id=20, user_id=1, moderator_id=0, action="kick", reason="", created_at=NOW)
    )
    assert len(await GetUserHistoryUseCase(uow_factory).execute(10, 1)) == 1
    assert len(await GetUserHistoryUseCase(uow_factory).execute(20, 1)) == 1
