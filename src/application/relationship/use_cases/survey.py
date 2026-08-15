"""Анкета знакомства (/introduce): одиночные выборы, тумблеры интересов и
кнопка «Готово» с разовым бонусом очков и пересчётом роли/лидерства."""

from dataclasses import dataclass
from datetime import datetime

from src.domain.relationship.events import RelationshipRoleChanged
from src.domain.relationship.policies import PointsToLevelPolicy

from ._common import SurveyData, UowFactory, _policy_of, _reevaluate_exclusive, _survey_of

_SURVEY_CHOICE_FIELDS = {
    "gender": "survey_gender",
    "contact": "survey_contact",
    "season": "survey_season",
}


class SetSurveyChoiceUseCase:
    """Одиночный выбор анкеты (пол / внимание / время года)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int, field: str, value: str) -> None:
        attr = _SURVEY_CHOICE_FIELDS[field]
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get_or_create(user_id, guild_id)
            setattr(profile, attr, value[:50])
            await uow.relationships.save(profile)
            await uow.commit()


class ToggleSurveyInterestUseCase:
    """Интерес-тумблер; возвращает (добавлен ли, актуальный список)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int, interest: str) -> tuple[bool, list[str]]:
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get_or_create(user_id, guild_id)
            interests = [i for i in profile.survey_interests.split(",") if i]
            if interest in interests:
                interests.remove(interest)
                added = False
            else:
                interests.append(interest)
                added = True
            profile.survey_interests = ",".join(interests)[:500]
            await uow.relationships.save(profile)
            await uow.commit()
            return added, interests


@dataclass(frozen=True)
class SurveyCompleteResult:
    first_time: bool
    bonus_awarded: int
    survey: SurveyData


class CompleteSurveyUseCase:
    """Кнопка «Готово»: разовый бонус очков (вне дневного потолка) +
    тот же пересчёт роли и лидерства, что и у обычного начисления."""

    def __init__(
        self,
        uow_factory: UowFactory,
        policy: PointsToLevelPolicy,
        bonus: int,
        settings_provider=None,
    ):
        self._uow_factory = uow_factory
        self._policy = policy
        self._bonus = bonus
        self._settings = settings_provider

    async def execute(self, user_id: int, guild_id: int, now: datetime) -> SurveyCompleteResult:
        policy = _policy_of(self._settings, guild_id, self._policy)
        bonus_amount = self._bonus
        if self._settings is not None:
            bonus_amount = self._settings.resolved(guild_id).survey_bonus_points
        async with self._uow_factory() as uow:
            profile = await uow.relationships.get_or_create(user_id, guild_id)
            first_time = profile.survey_completed_at is None
            bonus = 0
            if first_time:
                profile.survey_completed_at = now
                if not profile.frozen_by_admin:
                    bonus = bonus_amount
                    old_role = policy.role_index(profile.points, profile.is_exclusive)
                    profile.points += bonus
                    holder = await uow.relationships.get_exclusive_holder(guild_id)
                    previous_holder = _reevaluate_exclusive(
                        profile, holder, policy.exclusive_threshold
                    )
                    if previous_holder is not None:
                        await uow.relationships.save(previous_holder)
                    new_role = policy.role_index(profile.points, profile.is_exclusive)
                    if new_role != old_role:
                        uow.add_event(
                            RelationshipRoleChanged(
                                aggregate_id=f"{guild_id}:{user_id}",
                                guild_id=guild_id,
                                user_id=user_id,
                                old_role_index=old_role,
                                new_role_index=new_role,
                                points=profile.points,
                            )
                        )
            await uow.relationships.save(profile)
            await uow.commit()
            return SurveyCompleteResult(
                first_time=first_time,
                bonus_awarded=bonus,
                survey=_survey_of(profile),
            )
