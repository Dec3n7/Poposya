"""Общее для use-case'ов отношений: тип фабрики UoW, резолв политики ролей,
модель анкеты и правило титула «Единственного» - используются несколькими
группами (points/survey/decay), поэтому вынесены сюда."""

from collections.abc import Callable
from dataclasses import dataclass

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.relationship.entities import RelationshipProfile
from src.domain.relationship.policies import PointsToLevelPolicy

UowFactory = Callable[[], IUnitOfWork]


def _policy_of(
    settings_provider, guild_id: int, fallback: PointsToLevelPolicy
) -> PointsToLevelPolicy:
    """Политика ролей сервера (пороги/эксклюзив из /config) или глобальный
    фолбэк, если провайдер настроек не подключён (напр. в юнит-тестах)."""
    if settings_provider is not None:
        return settings_provider.resolved(guild_id).points_policy()
    return fallback


@dataclass(frozen=True)
class SurveyData:
    gender: str = ""
    contact: str = ""  # "quiet" | "normal" | "attention" ("" = normal)
    interests: str = ""
    season: str = ""
    completed: bool = False


def _survey_of(profile: RelationshipProfile) -> SurveyData:
    return SurveyData(
        gender=profile.survey_gender,
        contact=profile.survey_contact,
        interests=profile.survey_interests,
        season=profile.survey_season,
        completed=profile.survey_completed_at is not None,
    )


def _reevaluate_exclusive(
    profile: RelationshipProfile,
    holder: RelationshipProfile | None,
    exclusive_threshold: int,
) -> RelationshipProfile | None:
    """Правило «Единственного»: титул у лидера с очками >= порога; переходит
    только при СТРОГОМ превышении очков держателя (защита от мигания).
    Возвращает бывшего держателя, если титул перешёл."""
    if profile.points < exclusive_threshold or profile.is_exclusive:
        return None
    if holder is None:
        profile.is_exclusive = True
        return None
    if profile.points > holder.points:
        holder.is_exclusive = False
        profile.is_exclusive = True
        return holder
    return None
