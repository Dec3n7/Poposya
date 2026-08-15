"""Use-case'ы отношений, разбитые по темам (points / profile / birthday /
survey / secret_room / decay).

Этот __init__ ре-экспортит все имена, поэтому старые импорты
`from src.application.relationship.use_cases import X` продолжают работать
без изменений."""

from ._common import (
    SurveyData,
    UowFactory,
    _policy_of,
    _reevaluate_exclusive,
    _survey_of,
)
from .birthday import (
    BirthdayEvents,
    BirthdayTickUseCase,
    SetBirthdayUseCase,
    UpcomingBirthdaysUseCase,
    _days_until_birthday,
)
from .decay import DecayPointsUseCase, DecayResult
from .points import (
    AwardPointUseCase,
    AwardResult,
    GetLeaderboardUseCase,
    GetRankUseCase,
    LeaderboardEntry,
    RankInfo,
    SetPointsUseCase,
    ToggleFreezeUseCase,
)
from .profile import (
    AddDialogSummaryUseCase,
    ListProfilesUseCase,
    ProfileSummary,
    RecordDeepDialogUseCase,
    UpdateUserNotesUseCase,
)
from .secret_room import (
    GetSecretCodeUseCase,
    IssueSecretCodeUseCase,
    PopExpiredSecretRoomsUseCase,
    RedeemCheck,
    RegisterSecretRoomUseCase,
    ValidateSecretCodeUseCase,
)
from .survey import (
    _SURVEY_CHOICE_FIELDS,
    CompleteSurveyUseCase,
    SetSurveyChoiceUseCase,
    SurveyCompleteResult,
    ToggleSurveyInterestUseCase,
)

__all__ = [
    # общее
    "UowFactory",
    "SurveyData",
    "_policy_of",
    "_survey_of",
    "_reevaluate_exclusive",
    # points
    "AwardResult",
    "RankInfo",
    "LeaderboardEntry",
    "AwardPointUseCase",
    "GetRankUseCase",
    "SetPointsUseCase",
    "ToggleFreezeUseCase",
    "GetLeaderboardUseCase",
    # profile
    "ProfileSummary",
    "ListProfilesUseCase",
    "UpdateUserNotesUseCase",
    "RecordDeepDialogUseCase",
    "AddDialogSummaryUseCase",
    # birthday
    "SetBirthdayUseCase",
    "UpcomingBirthdaysUseCase",
    "BirthdayEvents",
    "BirthdayTickUseCase",
    "_days_until_birthday",
    # survey
    "SetSurveyChoiceUseCase",
    "ToggleSurveyInterestUseCase",
    "SurveyCompleteResult",
    "CompleteSurveyUseCase",
    "_SURVEY_CHOICE_FIELDS",
    # secret_room
    "RedeemCheck",
    "IssueSecretCodeUseCase",
    "ValidateSecretCodeUseCase",
    "RegisterSecretRoomUseCase",
    "GetSecretCodeUseCase",
    "PopExpiredSecretRoomsUseCase",
    # decay
    "DecayResult",
    "DecayPointsUseCase",
]
