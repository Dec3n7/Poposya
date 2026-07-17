"""Поведение под конкурентными транзакциями — только на PostgreSQL.

На SQLite эти тесты бессмысленны: глобальный лок записи сериализует всё, и
любая гонка «сама собой» исчезает. Поэтому весь модуль пропускается, если
TEST_DATABASE_URL не указывает на Postgres — ровно ту БД, где живёт бот.

Что здесь проверяется и зачем:
- где первичный ключ реально защищает от задвоения (альбом, авто-кик) —
  и какой ценой (проигравший падает с IntegrityError, а не получает False);
- где UPDATE существующей строки ключом НЕ защищён и теряет данные под
  гонкой (накопительный войс-итог) — это обоснование инварианта «один
  писатель», а не приглашение к многопоточной записи;
- что claim каморки при гонке оставляет ровно одного владельца.

Тесты с ручным чередованием (два UoW открыты одновременно) детерминированы:
оба читают до того, как кто-то запишет. Тесты через asyncio.gather проверяют
инвариант «наружу» — при любом исходе гонки, не завися от точного порядка.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from src.application.activity.use_cases import (
    SaveVoiceProgressUseCase,
    TryMarkAlbumPostUseCase,
)
from src.application.staykick.use_cases import SchedulePendingKickUseCase
from src.application.tempvoice.use_cases import (
    ClaimTempChannelUseCase,
    GetTempChannelUseCase,
    RegisterTempChannelUseCase,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="гонки воспроизводятся только на Postgres; SQLite сериализует записи",
)

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


# --- альбом: первичный ключ — настоящая защита от задвоения ---


async def test_album_second_writer_hits_primary_key(uow_factory):
    """Оба читают «ещё не постили», оба пишут — но PK (guild_id, message_id)
    не даёт вставить дважды: проигравший падает на commit."""
    async with uow_factory() as u1, uow_factory() as u2:
        assert not await u1.album_posts.was_posted(10, 555)
        assert not await u2.album_posts.was_posted(10, 555)  # оба видят пусто
        await u1.album_posts.mark_posted(10, 555, NOW)
        await u1.commit()  # первый успел
        await u2.album_posts.mark_posted(10, 555, NOW)
        with pytest.raises(IntegrityError):
            await u2.commit()  # второй — в PK


async def test_album_use_case_marks_exactly_once_under_load(uow_factory):
    """Инвариант, который волнует пользователя: сообщение попадает в альбом
    ровно один раз, сколько бы реакций ни прилетело одновременно."""
    uc = TryMarkAlbumPostUseCase(uow_factory)
    results = await asyncio.gather(
        *[uc.execute(10, 777, NOW) for _ in range(5)],
        return_exceptions=True,
    )
    assert sum(r is True for r in results) == 1  # ровно один «да, постим»
    # проигравшие — либо чистый False, либо IntegrityError (см. коммент в конце
    # файла: use case НЕ гасит это исключение, ког должен быть к нему готов)
    for r in results:
        assert r is True or r is False or isinstance(r, IntegrityError)


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


# --- войс-итог: read-modify-write, который PK НЕ защищает ---


async def test_voice_accrual_lost_update_is_real(uow_factory):
    """total_minutes += accrued — чтение, сложение, запись. Два писателя
    читают одно значение и один инкремент теряется. Тест доказывает риск,
    из-за которого войс-минуты копит РОВНО ОДИН фоновый цикл."""
    await SaveVoiceProgressUseCase(uow_factory).execute({(10, 1): 0.0})  # total=0
    async with uow_factory() as u1, uow_factory() as u2:
        # оба читают total=0 (READ COMMITTED: чужой незакоммиченный апдейт не виден)
        await u1.voice_progress.save_many({(10, 1): 5.0}, accrued_minutes=5.0)
        await u2.voice_progress.save_many({(10, 1): 5.0}, accrued_minutes=5.0)
        await u1.commit()  # total=5
        await u2.commit()  # тоже пишет 5 поверх — не 10
    async with uow_factory() as uow:
        total = await uow.voice_progress.total_minutes(10, 1)
    assert total == 5.0  # один +5 потерян; «правильные» 10 получились бы у одного писателя


async def test_voice_accrual_correct_when_serialized(uow_factory):
    """Тот же сценарий последовательно (как в проде — один цикл): итог верный.
    Контраст к тесту выше: проблема именно в конкуренции, а не в логике."""
    save = SaveVoiceProgressUseCase(uow_factory)
    await save.execute({(10, 1): 0.0})
    await save.execute({(10, 1): 5.0}, accrued_minutes=5.0)
    await save.execute({(10, 1): 5.0}, accrued_minutes=5.0)
    async with uow_factory() as uow:
        assert await uow.voice_progress.total_minutes(10, 1) == 10.0
