from dataclasses import dataclass

from src.application.appeals.use_cases import (
    CreateAppealUseCase,
    ListPendingAppealsUseCase,
    ResolveAppealUseCase,
)


@dataclass(frozen=True)
class AppealsContainer:
    create: CreateAppealUseCase
    resolve: ResolveAppealUseCase
    list_pending: ListPendingAppealsUseCase
