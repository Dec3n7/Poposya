"""Сценарии отношений поверх реального UoW+SQLite: ранг, админ-коррекция,
заморозка, заметки, ДР, лидерборд, угасание, анкета, секретные комнаты."""
from datetime import datetime, timedelta, timezone

import pytest

from src.application.relationship.use_cases import (
    AddDialogSummaryUseCase,
    CompleteSurveyUseCase,
    DecayPointsUseCase,
    GetLeaderboardUseCase,
    GetRankUseCase,
    GetSecretCodeUseCase,
    IssueSecretCodeUseCase,
    PopExpiredSecretRoomsUseCase,
    RecordDeepDialogUseCase,
    RegisterSecretRoomUseCase,
    SetBirthdayUseCase,
    SetPointsUseCase,
    SetSurveyChoiceUseCase,
    BirthdayTickUseCase,
    ToggleFreezeUseCase,
    ToggleSurveyInterestUseCase,
    UpdateUserNotesUseCase,
    ValidateSecretCodeUseCase,
)
from src.domain.relationship.events import ExclusiveTransferred, RelationshipRoleChanged
from src.domain.relationship.policies import PointsToLevelPolicy

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
POLICY = PointsToLevelPolicy()


# --- GetRank / SetPoints / Freeze ------------------------------------------

async def test_get_rank_for_unknown_user(uow_factory):
    rank = await GetRankUseCase(uow_factory, POLICY).execute(1, 10)
    assert rank.points == 0
    assert rank.level == 1
    assert rank.role_index is None
    assert rank.next_threshold == 100
    assert rank.frozen is False


async def test_set_points_updates_rank_and_emits_role_event(uow_factory, event_bus):
    events = []
    event_bus.subscribe(RelationshipRoleChanged, lambda e: events.append(e))

    rank = await SetPointsUseCase(uow_factory, POLICY).execute(1, 10, 300)
    assert rank.points == 300
    assert rank.role_index == 1
    assert len(events) == 1 and events[0].new_role_index == 1

    # проверяем персистентность через GetRank
    again = await GetRankUseCase(uow_factory, POLICY).execute(1, 10)
    assert again.points == 300


async def test_set_points_clamps_negative(uow_factory):
    rank = await SetPointsUseCase(uow_factory, POLICY).execute(1, 10, -50)
    assert rank.points == 0


async def test_set_points_can_grant_exclusive(uow_factory):
    rank = await SetPointsUseCase(uow_factory, POLICY).execute(1, 10, 2000)
    assert rank.is_exclusive is True
    assert rank.level == 7


async def test_toggle_freeze(uow_factory):
    toggle = ToggleFreezeUseCase(uow_factory)
    assert await toggle.execute(1, 10) is True
    assert await toggle.execute(1, 10) is False
    assert (await GetRankUseCase(uow_factory, POLICY).execute(1, 10)).frozen is False


async def test_update_user_notes_trimmed_and_capped(uow_factory):
    await UpdateUserNotesUseCase(uow_factory, max_chars=10).execute(1, 10, "  привет мир большой  ")
    rank = await GetRankUseCase(uow_factory, POLICY).execute(1, 10)
    assert rank.user_notes == "привет мир"  # обрезано до 10 символов после strip


# --- Birthdays --------------------------------------------------------------

async def test_set_birthday_valid_and_invalid(uow_factory):
    setter = SetBirthdayUseCase(uow_factory)
    assert await setter.execute(1, 10, 29, 2) is True   # високосный допустим
    assert await setter.execute(1, 10, 31, 2) is False  # 31 февраля нет
    rank = await GetRankUseCase(uow_factory, POLICY).execute(1, 10)
    assert (rank.birthday_day, rank.birthday_month) == (29, 2)


async def test_birthday_tick_reminds_and_congratulates(uow_factory):
    await SetBirthdayUseCase(uow_factory).execute(1, 10, 14, 7)   # ДР 14 июля
    await SetBirthdayUseCase(uow_factory).execute(2, 10, 11, 7)   # ДР сегодня 11 июля

    tick = BirthdayTickUseCase(uow_factory, remind_days=3)  # напоминаем за 3 дня -> 14 июля
    events = await tick.execute(NOW)
    assert (10, 1) in events.remind
    assert (10, 2) in events.congratulate

    # повторный тик в тот же год — дедупликация, пусто
    events2 = await tick.execute(NOW)
    assert events2.remind == [] and events2.congratulate == []


# --- Leaderboard ------------------------------------------------------------

async def test_leaderboard_sorted_desc(uow_factory):
    setter = SetPointsUseCase(uow_factory, POLICY)
    await setter.execute(1, 10, 100)
    await setter.execute(2, 10, 500)
    await setter.execute(3, 10, 300)
    await setter.execute(4, 20, 999)  # другая гильдия

    board = await GetLeaderboardUseCase(uow_factory, POLICY).execute(10, limit=10)
    assert [e.user_id for e in board] == [2, 3, 1]
    assert board[0].points == 500


# --- Decay ------------------------------------------------------------------

async def test_decay_reduces_points_after_inactivity(uow_factory):
    # профиль с очками и старым диалогом
    await SetPointsUseCase(uow_factory, POLICY).execute(1, 10, 300)
    # проставим last_dialog_at напрямую в прошлом
    from src.application.relationship.use_cases import AwardPointUseCase
    old = NOW - timedelta(days=40)
    await AwardPointUseCase(uow_factory, POLICY, daily_cap=20, absence_days=30).execute(1, 10, 0, old)

    decay = DecayPointsUseCase(uow_factory, POLICY, after_days=30, every_days=7, amount=10)
    result = await decay.execute(NOW)
    assert result.decayed == 1
    rank = await GetRankUseCase(uow_factory, POLICY).execute(1, 10)
    assert rank.points == 291  # 300 + 1 (award) - 10


async def test_decay_skips_recent_activity(uow_factory):
    from src.application.relationship.use_cases import AwardPointUseCase
    await AwardPointUseCase(uow_factory, POLICY, daily_cap=20, absence_days=30).execute(1, 10, 0, NOW)
    decay = DecayPointsUseCase(uow_factory, POLICY, after_days=30, every_days=7, amount=10)
    result = await decay.execute(NOW)
    assert result.decayed == 0


# --- Deep dialogs / summaries ----------------------------------------------

async def test_record_deep_dialog_increments(uow_factory):
    rec = RecordDeepDialogUseCase(uow_factory)
    await rec.execute(1, 10)
    await rec.execute(1, 10)
    rank = await GetRankUseCase(uow_factory, POLICY).execute(1, 10)
    assert rank.deep_dialogs == 2


async def test_dialog_summaries_keep_last_n(uow_factory):
    add = AddDialogSummaryUseCase(uow_factory, keep=2)
    for i in range(4):
        await add.execute(1, 10, f"память {i}", NOW + timedelta(minutes=i))
    # проверяем через репозиторий напрямую
    async with uow_factory() as uow:
        last = await uow.dialog_summaries.last(10, 1, 10)
    assert last == ["память 2", "память 3"]  # только последние 2, в порядке старые->новые


# --- Survey -----------------------------------------------------------------

async def test_survey_choice_and_interest_toggle(uow_factory):
    await SetSurveyChoiceUseCase(uow_factory).execute(1, 10, "gender", "девушка")
    toggle = ToggleSurveyInterestUseCase(uow_factory)
    added, interests = await toggle.execute(1, 10, "музыка")
    assert added is True and interests == ["музыка"]
    added, interests = await toggle.execute(1, 10, "музыка")  # повторно — снимаем
    assert added is False and interests == []

    rank = await GetRankUseCase(uow_factory, POLICY).execute(1, 10)
    assert rank.survey.gender == "девушка"


async def test_complete_survey_grants_bonus_once(uow_factory):
    complete = CompleteSurveyUseCase(uow_factory, POLICY, bonus=50)
    r1 = await complete.execute(1, 10, NOW)
    assert r1.first_time is True and r1.bonus_awarded == 50
    r2 = await complete.execute(1, 10, NOW)
    assert r2.first_time is False and r2.bonus_awarded == 0

    rank = await GetRankUseCase(uow_factory, POLICY).execute(1, 10)
    assert rank.points == 50


async def test_complete_survey_frozen_no_bonus(uow_factory):
    await ToggleFreezeUseCase(uow_factory).execute(1, 10)
    r = await CompleteSurveyUseCase(uow_factory, POLICY, bonus=50).execute(1, 10, NOW)
    assert r.first_time is True and r.bonus_awarded == 0
    rank = await GetRankUseCase(uow_factory, POLICY).execute(1, 10)
    assert rank.points == 0


# --- Secret rooms -----------------------------------------------------------

async def test_issue_secret_code_stable_until_used(uow_factory):
    issue = IssueSecretCodeUseCase(uow_factory)
    code1 = await issue.execute(1, 10, NOW)
    code2 = await issue.execute(1, 10, NOW)
    assert code1 == code2  # неиспользованный ключ переиспользуется
    assert "-" in code1

    stored = await GetSecretCodeUseCase(uow_factory).execute(1, 10)
    assert stored is not None and stored.code == code1 and stored.used_at is None


async def test_validate_secret_code_flow(uow_factory):
    validate = ValidateSecretCodeUseCase(uow_factory)
    # нет кода
    assert (await validate.execute(1, 10, "XXXX-YYYY", NOW)).reason == "no_code"

    code = await IssueSecretCodeUseCase(uow_factory).execute(1, 10, NOW)
    # неверный код
    assert (await validate.execute(1, 10, "WRON-GONE", NOW)).reason == "wrong"
    # верный код (регистр не важен)
    assert (await validate.execute(1, 10, code.lower(), NOW)).ok is True


async def test_register_room_marks_code_used_and_blocks_new_redeem(uow_factory):
    code = await IssueSecretCodeUseCase(uow_factory).execute(1, 10, NOW)
    expires = await RegisterSecretRoomUseCase(uow_factory, hours=6).execute(
        1, 10, text_channel_id=111, voice_channel_id=222, now=NOW
    )
    assert expires == NOW + timedelta(hours=6)

    # ключ помечен использованным
    stored = await GetSecretCodeUseCase(uow_factory).execute(1, 10)
    assert stored.used_at is not None

    # пока комната активна — любой редим упирается в room_active (проверяется первым)
    check = await ValidateSecretCodeUseCase(uow_factory).execute(2, 10, "any", NOW)
    assert check.reason == "room_active" and check.active_room_channel_id == 111

    # когда комната истекла и убрана — виден статус использованного ключа
    later = NOW + timedelta(hours=7)
    await PopExpiredSecretRoomsUseCase(uow_factory).execute(later)
    assert (await ValidateSecretCodeUseCase(uow_factory).execute(1, 10, code, later)).reason == "used"


async def test_pop_expired_secret_rooms(uow_factory):
    await RegisterSecretRoomUseCase(uow_factory, hours=1).execute(
        1, 10, text_channel_id=111, voice_channel_id=222, now=NOW
    )
    later = NOW + timedelta(hours=2)
    expired = await PopExpiredSecretRoomsUseCase(uow_factory).execute(later)
    assert len(expired) == 1 and expired[0].text_channel_id == 111
    # повторно — пусто
    assert await PopExpiredSecretRoomsUseCase(uow_factory).execute(later) == []
