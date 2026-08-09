"""Достижения: чистый домен (эвалуатор, каталог) + персист/логика на реальной БД.

Каталог проверяем на целостность (уникальность id, валидные тиры, иконки из
рендера). Логику — сквозняком: сид существующих репозиториев → EvaluateAchievements
открывает заслуженное, повторный прогон не дублирует.
"""

from datetime import UTC, datetime

from src.application.achievements.use_cases import (
    EvaluateAchievementsUseCase,
    GetAchievementsUseCase,
)
from src.domain.achievements.catalog import BY_ID, CATALOG
from src.domain.achievements.entities import (
    Tier,
    UnlockedAchievement,
    UserStats,
    newly_unlocked,
)
from src.domain.finds.entities import CollectionItem
from src.domain.music.entities import LikedTrack
from src.domain.relationship.entities import RelationshipProfile
from src.domain.relationship.policies import PointsToLevelPolicy
from src.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from src.infrastructure.events.in_memory_bus import InMemoryEventBus

_POLICY = PointsToLevelPolicy(thresholds=(100, 250, 450, 700, 950, 1200), exclusive_threshold=1250)


# ── чистый домен ──────────────────────────────────────────────────────────────


def test_newly_unlocked_filters_by_condition_and_already_owned():
    stats = UserStats(finds_count=1)
    fresh = newly_unlocked(stats, CATALOG, already_unlocked=set())
    ids = {a.id for a in fresh}
    assert "finds_first" in ids  # одна находка — открыто
    assert "finds_10" not in ids  # десяти нет
    # уже открытое не выдаётся повторно
    again = newly_unlocked(stats, CATALOG, already_unlocked={"finds_first"})
    assert "finds_first" not in {a.id for a in again}


def test_catalog_integrity():
    ids = [a.id for a in CATALOG]
    assert len(ids) == len(set(ids)), "id ачивок должны быть уникальны"
    assert len(BY_ID) == len(CATALOG)
    for a in CATALOG:
        assert a.tier in set(Tier)
        assert a.icon, f"у ачивки {a.id} нет эмодзи-эмблемы"  # непустая эмодзи
        assert a.stat_label and a.name and a.description


def test_stat_getters_read_from_stats():
    deep = BY_ID["rel_deep_5"]
    assert deep.stat(UserStats(deep_dialogs=7)) == 7
    voice = BY_ID["voice_50"]
    assert voice.stat(UserStats(voice_hours=63.9)) == 63  # часы округляются вниз


# ── логика на реальной БД ─────────────────────────────────────────────────────


def _uow_factory(session_factory):
    bus = InMemoryEventBus()
    return lambda: SqlAlchemyUnitOfWork(session_factory, bus)


async def _seed(session_factory):
    uowf = _uow_factory(session_factory)
    async with uowf() as uow:
        await uow.relationships.save(
            RelationshipProfile(
                user_id=1,
                guild_id=10,
                points=500,
                deep_dialogs=6,
                is_exclusive=True,
                survey_completed_at=datetime(2026, 1, 1),
            )
        )
        await uow.collections.add(
            CollectionItem(
                guild_id=10, user_id=1, item_id="unsent_letter", obtained_at=datetime(2026, 1, 1)
            )
        )
        await uow.liked_tracks.add(
            LikedTrack(
                user_id=1,
                video_id="v1",
                title="t",
                uploader="u",
                duration=100,
                liked_at=datetime(2026, 1, 1),
            )
        )
        await uow.commit()


async def test_evaluate_unlocks_earned_and_is_idempotent(session_factory):
    await _seed(session_factory)
    evaluate = EvaluateAchievementsUseCase(_uow_factory(session_factory), _POLICY)

    result = await evaluate.execute(user_id=1, guild_id=10)
    ids = {a.id for a in result.unlocked}
    assert {"rel_survey", "rel_deep_5", "rel_exclusive", "finds_first", "finds_legendary"} <= ids
    assert "finds_10" not in ids  # одна находка
    assert "music_likes_50" not in ids  # один лайк
    assert "voice_50" not in ids  # ноль часов
    assert result.stats.finds_count == 1 and result.stats.likes_count == 1

    # повторный прогон — ничего нового
    again = await evaluate.execute(user_id=1, guild_id=10)
    assert again.unlocked == []


async def test_get_achievements_returns_unlocked_and_stats(session_factory):
    await _seed(session_factory)
    factory = _uow_factory(session_factory)
    await EvaluateAchievementsUseCase(factory, _POLICY).execute(1, 10)

    showcase = await GetAchievementsUseCase(factory, _POLICY).execute(1, 10)
    assert "rel_exclusive" in showcase.unlocked_ids
    assert showcase.stats.deep_dialogs == 6


async def test_repository_add_is_idempotent(session_factory):
    factory = _uow_factory(session_factory)
    now = datetime.now(UTC)
    async with factory() as uow:
        await uow.achievements.add(UnlockedAchievement(1, 10, "finds_first", now))
        await uow.achievements.add(UnlockedAchievement(1, 10, "finds_first", now))  # дубль
        await uow.commit()
    async with factory() as uow:
        assert await uow.achievements.unlocked_ids(1, 10) == {"finds_first"}
