"""Поведение под конкурентными транзакциями — только на PostgreSQL.

На SQLite эти тесты бессмысленны: глобальный лок записи сериализует всё, и
любая гонка «сама собой» исчезает. Поэтому весь модуль пропускается, если
TEST_DATABASE_URL не указывает на Postgres — ровно ту БД, где живёт бот.

Что здесь проверяется:
- альбом: try_mark (INSERT ON CONFLICT DO NOTHING) под гонкой даёт ровно одну
  True без исключений — атомарно, не «проверить-потом-вставить»;
- войс-итог: total += accrued считает сама БД (ON CONFLICT DO UPDATE), поэтому
  конкурентные инкременты не теряются (строгий тест: 10 параллельных +1 = 10);
- авто-кик: одна запись на пару сервер+человек переживает гонку;
- claim каморки: при одновременном «Забрать» ровно один владелец, без порчи.

Гонки проверяются через asyncio.gather (отдельные задачи): фикс заставляет
писателей блокироваться на строке, поэтому ручное чередование в одной корутине
привело бы к дедлоку.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest

from src.application.activity.use_cases import (
    SaveVoiceProgressUseCase,
    TryMarkAlbumPostUseCase,
)
from src.application.relationship.use_cases import (
    AwardPointUseCase,
    GetRankUseCase,
    SetPointsUseCase,
)
from src.application.staykick.use_cases import SchedulePendingKickUseCase
from src.application.tempvoice.use_cases import (
    ClaimTempChannelUseCase,
    GetTempChannelUseCase,
    RegisterTempChannelUseCase,
)
from src.domain.relationship.policies import PointsToLevelPolicy

POLICY = PointsToLevelPolicy()

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="гонки воспроизводятся только на Postgres; SQLite сериализует записи",
)

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


async def _mark(uow_factory, guild_id: int, message_id: int) -> bool:
    """Одна пометка альбома в собственной транзакции — для gather."""
    async with uow_factory() as uow:
        marked = await uow.album_posts.try_mark(guild_id, message_id, NOW)
        await uow.commit()
        return marked


# --- альбом: атомарный try_mark вместо «проверить-потом-вставить» ---


async def test_album_try_mark_atomic_second_gets_false(uow_factory):
    """try_mark (INSERT ON CONFLICT DO NOTHING) под параллельной нагрузкой даёт
    ровно одну True, остальные — чистый False, БЕЗ исключений.

    Смоук, а не строгий ловец регрессии: с фиксом писатели блокируются на строке
    (детерминированно чередовать нельзя — дедлок в одной корутине), а gather
    планировщик часто сериализует, так что старый check-then-act здесь тоже
    прошёл бы. Функциональную дедупликацию строго проверяет test_album_post_dedup,
    потерю апдейта — test_voice_accrual_correct_under_concurrency."""
    results = await asyncio.gather(*[_mark(uow_factory, 10, 555) for _ in range(5)])
    assert sum(results) == 1  # ровно один True
    assert all(r in (True, False) for r in results)  # никаких исключений


async def test_album_use_case_marks_exactly_once_under_load(uow_factory):
    """Инвариант, который волнует пользователя: сообщение попадает в альбом
    ровно один раз, сколько бы реакций ни прилетело одновременно. Проигравшие
    получают чистый False — ког больше не должен ловить IntegrityError."""
    uc = TryMarkAlbumPostUseCase(uow_factory)
    results = await asyncio.gather(*[uc.execute(10, 777, NOW) for _ in range(5)])
    assert sum(r is True for r in results) == 1  # ровно один «да, постим»
    assert all(r is False for r in results if r is not True)  # без исключений


# --- авто-кик: тот же PK-щит (одна запись на пару сервер+человек) ---


async def test_concurrent_schedule_leaves_exactly_one_kick(uow_factory):
    """delete+add на один ключ из нескольких транзакций разом. Часть упрётся
    в PK, но итог всегда один: двух авто-киков одному человеку не бывает."""
    uc = SchedulePendingKickUseCase(uow_factory)
    await asyncio.gather(
        *[
            uc.execute(guild_id=10, user_id=1, now=NOW, hours=12, remind_before_minutes=60)
            for _ in range(5)
        ],
        return_exceptions=True,
    )
    async with uow_factory() as uow:
        due = await uow.pending_kicks.pop_due_kicks(NOW + timedelta(hours=13))
    assert len(due) == 1  # ровно одна запись пережила гонку


# --- каморка: claim при гонке — один владелец, без порчи ---


async def test_concurrent_claim_leaves_single_owner(uow_factory):
    """Двое одновременно жмут «Забрать» брошенную каморку. Слепой UPDATE по PK
    даёт last-write-wins: в БД ровно один владелец — из тех, кто забирал."""
    await RegisterTempChannelUseCase(uow_factory).execute(10, 100, owner_id=999, now=NOW)
    claim = ClaimTempChannelUseCase(uow_factory)
    a, b = await asyncio.gather(
        claim.execute(100, new_owner_id=1, present_ids={1}),
        claim.execute(100, new_owner_id=2, present_ids={2}),
    )
    final = await GetTempChannelUseCase(uow_factory).execute(100)
    assert final.owner_id in (1, 2)  # владелец один и валидный, не 999 и не мусор
    # оба вызова вернули ok — каждый решал по своему чтению; это осознанный
    # last-write-wins, не порча: в канале в любом случае один хозяин
    assert a.ok and b.ok


# --- войс-итог: атомарный инкремент в БД, инкременты не теряются ---


async def test_voice_accrual_correct_under_concurrency(uow_factory):
    """total_minutes += accrued считает сама БД (INSERT ON CONFLICT DO UPDATE
    SET total = total + accrued), а не Python после чтения. Поэтому писатели не
    теряют инкремент: каждый блокируется на строке до коммита предыдущего и
    прибавляет к УЖЕ обновлённому значению.

    10 одновременных +1: с атомарным инкрементом итог всегда 10; при старом
    read-modify-write почти наверняка меньше (потерянные апдейты)."""
    await SaveVoiceProgressUseCase(uow_factory).execute({(10, 1): 0.0})  # total=0
    save = SaveVoiceProgressUseCase(uow_factory)
    await asyncio.gather(*[save.execute({(10, 1): 1.0}, accrued_minutes=1.0) for _ in range(10)])
    async with uow_factory() as uow:
        total = await uow.voice_progress.total_minutes(10, 1)
    assert total == 10.0  # все 10 инкрементов учтены


async def test_voice_accrual_correct_when_serialized(uow_factory):
    """Тот же сценарий последовательно (как в проде — один цикл): итог верный."""
    save = SaveVoiceProgressUseCase(uow_factory)
    await save.execute({(10, 1): 0.0})
    await save.execute({(10, 1): 5.0}, accrued_minutes=5.0)
    await save.execute({(10, 1): 5.0}, accrued_minutes=5.0)
    async with uow_factory() as uow:
        assert await uow.voice_progress.total_minutes(10, 1) == 10.0


# --- отношения: очки не теряются под двумя писателями (FOR UPDATE в get_or_create) ---


async def test_award_points_no_lost_updates_under_concurrency(uow_factory):
    """N параллельных начислений одному человеку дают ровно N очков.

    Строгий ловец регрессии для главного риска перед веб-панелью: раньше
    AwardPointUseCase делал read-modify-write (get_or_create читает снимок ->
    +1 в памяти -> save перезаписывает всю строку), и два писателя теряли
    инкремент. Теперь get_or_create берёт строку под SELECT ... FOR UPDATE:
    второй писатель ждёт коммита первого и прибавляет к свежему значению.

    daily_cap намеренно большой — проверяем именно потерю апдейта, а не потолок."""
    award = AwardPointUseCase(uow_factory, POLICY, daily_cap=1000, absence_days=30)
    await asyncio.gather(*[award.execute(1, 10, 0, NOW) for _ in range(10)])
    rank = await GetRankUseCase(uow_factory, POLICY).execute(1, 10)
    assert rank.points == 10  # все 10 начислений учтены, ни одно не затёрто


async def test_admin_set_points_not_lost_to_concurrent_award(uow_factory):
    """Сценарий-смоук: админ правит очки (веб-панель), пока бот начисляет за
    сообщение. Итог должен быть валидным — 100 (award до set) или 101 (set до
    award), но НИКОГДА 1 (затёртая правка).

    Смоук, а не строгий ловец: всего две задачи, и планировщик gather часто
    сериализует их сам, так что старый код без блокировки здесь мог бы пройти.
    Строго потерю апдейта под нагрузкой ловит
    test_award_points_no_lost_updates_under_concurrency (10 писателей, падает на
    старом read-modify-write)."""
    # профиль существует (points=0), чтобы обе транзакции стартовали от одной базы
    await AwardPointUseCase(uow_factory, POLICY, daily_cap=1000, absence_days=30).execute(
        1, 10, 0, NOW - timedelta(days=1)
    )
    await SetPointsUseCase(uow_factory, POLICY).execute(1, 10, 0)

    award = AwardPointUseCase(uow_factory, POLICY, daily_cap=1000, absence_days=30)
    setter = SetPointsUseCase(uow_factory, POLICY)
    await asyncio.gather(award.execute(1, 10, 0, NOW), setter.execute(1, 10, 100))

    rank = await GetRankUseCase(uow_factory, POLICY).execute(1, 10)
    assert rank.points in (100, 101)  # админская правка пережила гонку
