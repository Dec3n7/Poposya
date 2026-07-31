from dataclasses import dataclass

from src.application.banwatch.use_cases import (
    CheckUserUseCase,
    FlaggedCandidatesUseCase,
    RecordBanUseCase,
    RemoveBanUseCase,
    SyncGuildBansUseCase,
)


@dataclass(frozen=True)
class BanwatchContainer:
    """Зависимости модуля «Кросс-серверные баны»; собирается в root_container и
    в api-контейнере (панель использует check_user/flagged)."""

    record_ban: RecordBanUseCase
    remove_ban: RemoveBanUseCase
    sync_guild: SyncGuildBansUseCase
    check_user: CheckUserUseCase
    flagged: FlaggedCandidatesUseCase
