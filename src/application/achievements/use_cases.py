"""Логика достижений: собрать снимок показателей участника из уже существующих
репозиториев и открыть всё заслуженное.

Один путь для трёх сценариев — событие, `/achievements`, бэкфилл: разница лишь
в том, кто зовёт. Персональных счётчиков не заводим (см. achievements.md).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.achievements.catalog import CATALOG
from src.domain.achievements.entities import (
    Achievement,
    UnlockedAchievement,
    UserStats,
    newly_unlocked,
)
from src.domain.finds.catalog import get_item
from src.domain.finds.entities import Rarity
from src.domain.relationship.entities import RelationshipProfile
from src.domain.relationship.policies import PointsToLevelPolicy

UowFactory = Callable[[], IUnitOfWork]


def _policy_of(
    settings_provider, guild_id: int, fallback: PointsToLevelPolicy
) -> PointsToLevelPolicy:
    """Пороги ролей сервера (/config) или глобальный фолбэк (в т.ч. в тестах)."""
    if settings_provider is not None:
        return settings_provider.resolved(guild_id).points_policy()
    return fallback


async def _collect_stats(
    uow: IUnitOfWork, user_id: int, guild_id: int, policy: PointsToLevelPolicy
) -> UserStats:
    profile = await uow.relationships.get(user_id, guild_id) or RelationshipProfile(
        user_id=user_id, guild_id=guild_id
    )
    collection = await uow.collections.list_for_user(guild_id, user_id)
    has_legendary = any(
        (item := get_item(entry.item_id)) is not None and item.rarity is Rarity.LEGENDARY
        for entry in collection
    )
    likes = await uow.liked_tracks.count(user_id)
    minutes = await uow.voice_progress.total_minutes(guild_id, user_id)
    return UserStats(
        points=profile.points,
        level=policy.level(profile.points, profile.is_exclusive),
        is_exclusive=profile.is_exclusive,
        deep_dialogs=profile.deep_dialogs,
        survey_completed=profile.survey_completed_at is not None,
        finds_count=len(collection),
        has_legendary_find=has_legendary,
        likes_count=likes,
        voice_hours=minutes / 60,
    )


@dataclass(frozen=True)
class EvalResult:
    unlocked: list[Achievement]  # открытые именно сейчас (для уведомлений)
    stats: UserStats  # снимок — для чисел на карточке


@dataclass(frozen=True)
class ShowcaseResult:
    unlocked_ids: set[str]  # что уже открыто (для локед/анлокед в витрине)
    stats: UserStats


class EvaluateAchievementsUseCase:
    """Пересчитать ачивки участника и выдать те, что открылись сейчас."""

    def __init__(
        self,
        uow_factory: UowFactory,
        policy: PointsToLevelPolicy,
        settings_provider=None,
        catalog: list[Achievement] = CATALOG,
    ):
        self._uow_factory = uow_factory
        self._policy = policy
        self._settings = settings_provider
        self._catalog = catalog

    async def execute(self, user_id: int, guild_id: int) -> EvalResult:
        policy = _policy_of(self._settings, guild_id, self._policy)
        async with self._uow_factory() as uow:
            stats = await _collect_stats(uow, user_id, guild_id, policy)
            unlocked_ids = await uow.achievements.unlocked_ids(user_id, guild_id)
            fresh = newly_unlocked(stats, self._catalog, unlocked_ids)
            now = datetime.now(UTC)
            for achievement in fresh:
                await uow.achievements.add(
                    UnlockedAchievement(user_id, guild_id, achievement.id, now)
                )
            await uow.commit()
        return EvalResult(unlocked=fresh, stats=stats)


class GetAchievementsUseCase:
    """Витрина: что открыто + снимок показателей для чисел (только чтение)."""

    def __init__(
        self, uow_factory: UowFactory, policy: PointsToLevelPolicy, settings_provider=None
    ):
        self._uow_factory = uow_factory
        self._policy = policy
        self._settings = settings_provider

    async def execute(self, user_id: int, guild_id: int) -> ShowcaseResult:
        policy = _policy_of(self._settings, guild_id, self._policy)
        async with self._uow_factory() as uow:
            stats = await _collect_stats(uow, user_id, guild_id, policy)
            unlocked_ids = await uow.achievements.unlocked_ids(user_id, guild_id)
        return ShowcaseResult(unlocked_ids=unlocked_ids, stats=stats)
