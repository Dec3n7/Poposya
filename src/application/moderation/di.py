from dataclasses import dataclass

from src.application.moderation.use_cases import (
    ClearWarnsUseCase,
    GetWarnsUseCase,
    ListTempBansUseCase,
    PopExpiredBansUseCase,
    RemoveTempBanUseCase,
    TempBanUserUseCase,
    WarnUserUseCase,
)


@dataclass(frozen=True)
class ModerationContainer:
    warn_user: WarnUserUseCase
    get_warns: GetWarnsUseCase
    clear_warns: ClearWarnsUseCase
    temp_ban: TempBanUserUseCase
    remove_ban: RemoveTempBanUseCase
    list_bans: ListTempBansUseCase
    pop_expired_bans: PopExpiredBansUseCase
