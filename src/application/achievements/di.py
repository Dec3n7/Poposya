from dataclasses import dataclass

from src.application.achievements.use_cases import (
    EvaluateAchievementsUseCase,
    GetAchievementsUseCase,
)


@dataclass(frozen=True)
class AchievementsContainer:
    evaluate: EvaluateAchievementsUseCase
    get: GetAchievementsUseCase
