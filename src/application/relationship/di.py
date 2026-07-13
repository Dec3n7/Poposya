from dataclasses import dataclass

from src.application.relationship.use_cases import (
    AddDialogSummaryUseCase,
    AwardPointUseCase,
    BirthdayTickUseCase,
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
    ToggleFreezeUseCase,
    ToggleSurveyInterestUseCase,
    UpdateUserNotesUseCase,
    ValidateSecretCodeUseCase,
)
from src.domain.relationship.policies import PointsToLevelPolicy


@dataclass(frozen=True)
class RelationshipContainer:
    policy: PointsToLevelPolicy
    award_point: AwardPointUseCase
    get_rank: GetRankUseCase
    set_points: SetPointsUseCase
    toggle_freeze: ToggleFreezeUseCase
    update_notes: UpdateUserNotesUseCase
    set_survey_choice: SetSurveyChoiceUseCase
    toggle_survey_interest: ToggleSurveyInterestUseCase
    complete_survey: CompleteSurveyUseCase
    set_birthday: SetBirthdayUseCase
    birthday_tick: BirthdayTickUseCase
    leaderboard: GetLeaderboardUseCase
    decay_points: DecayPointsUseCase
    record_deep_dialog: RecordDeepDialogUseCase
    add_dialog_summary: AddDialogSummaryUseCase
    issue_secret_code: IssueSecretCodeUseCase
    validate_secret_code: ValidateSecretCodeUseCase
    register_secret_room: RegisterSecretRoomUseCase
    get_secret_code: GetSecretCodeUseCase
    pop_expired_secret_rooms: PopExpiredSecretRoomsUseCase
    role_names: list[str]
