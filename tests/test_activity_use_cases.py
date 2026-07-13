"""Тесты активности поверх реального UoW+SQLite: возвращение после отсутствия,
напоминания, войс-минуты, дедуп альбомных постов."""

from datetime import datetime, timedelta, timezone

from src.application.activity.use_cases import (
    AddReminderUseCase,
    GetVoiceHoursUseCase,
    LoadVoiceProgressUseCase,
    PopDueRemindersUseCase,
    SaveVoiceProgressUseCase,
    TouchMemberActivityUseCase,
    TryMarkAlbumPostUseCase,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


async def test_first_touch_not_a_return(uow_factory):
    touch = TouchMemberActivityUseCase(uow_factory, absent_days_threshold=7)
    r = await touch.execute(1, 10, now=NOW)
    assert r.returned_after_absence is False
    assert r.days_absent == 0


async def test_touch_after_long_absence_flags_return(uow_factory):
    touch = TouchMemberActivityUseCase(uow_factory, absent_days_threshold=7)
    await touch.execute(1, 10, now=NOW)
    later = NOW + timedelta(days=10)
    r = await touch.execute(1, 10, now=later)
    assert r.returned_after_absence is True
    assert r.days_absent == 10


async def test_touch_short_absence_not_return(uow_factory):
    touch = TouchMemberActivityUseCase(uow_factory, absent_days_threshold=7)
    await touch.execute(1, 10, now=NOW)
    r = await touch.execute(1, 10, now=NOW + timedelta(days=3))
    assert r.returned_after_absence is False
    assert r.days_absent == 3


async def test_reminders_pop_due_only(uow_factory):
    add = AddReminderUseCase(uow_factory)
    await add.execute(1, 10, "скоро", due_at=NOW + timedelta(minutes=5))
    await add.execute(1, 10, "потом", due_at=NOW + timedelta(hours=5))

    due = await PopDueRemindersUseCase(uow_factory).execute(NOW + timedelta(minutes=10))
    assert [r.text for r in due] == ["скоро"]
    # повторный вызов — уже удалено
    assert await PopDueRemindersUseCase(uow_factory).execute(NOW + timedelta(minutes=10)) == []
    # отложенное всё ещё ждёт
    left = await PopDueRemindersUseCase(uow_factory).execute(NOW + timedelta(hours=6))
    assert [r.text for r in left] == ["потом"]


async def test_voice_progress_save_load_roundtrip(uow_factory):
    progress = {(10, 1): 30.0, (10, 2): 15.0}
    await SaveVoiceProgressUseCase(uow_factory).execute(progress, accrued_minutes=5.0)
    loaded = await LoadVoiceProgressUseCase(uow_factory).execute()
    assert loaded == progress


async def test_voice_progress_empty_is_noop(uow_factory):
    await SaveVoiceProgressUseCase(uow_factory).execute({})  # не должно падать
    assert await LoadVoiceProgressUseCase(uow_factory).execute() == {}


async def test_voice_hours_accumulate_totals(uow_factory):
    save = SaveVoiceProgressUseCase(uow_factory)
    await save.execute({(10, 1): 30.0}, accrued_minutes=60.0)
    await save.execute({(10, 1): 45.0}, accrued_minutes=60.0)
    hours = await GetVoiceHoursUseCase(uow_factory).execute(10, 1)
    assert hours == 2.0  # (60+60)/60


async def test_voice_hours_unknown_user_zero(uow_factory):
    assert await GetVoiceHoursUseCase(uow_factory).execute(10, 999) == 0.0


async def test_album_post_dedup(uow_factory):
    mark = TryMarkAlbumPostUseCase(uow_factory)
    assert await mark.execute(10, 555, now=NOW) is True
    # тот же message_id — уже публиковалось
    assert await mark.execute(10, 555, now=NOW) is False
    # другой message_id — новый
    assert await mark.execute(10, 556, now=NOW) is True
