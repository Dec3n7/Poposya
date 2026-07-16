from dataclasses import dataclass

from src.application.staykick.use_cases import (
    CancelPendingKickUseCase,
    DueRemindersUseCase,
    PopDueKicksUseCase,
    SchedulePendingKickUseCase,
)


@dataclass(frozen=True)
class StayKickContainer:
    schedule_kick: SchedulePendingKickUseCase
    cancel_kick: CancelPendingKickUseCase
    pop_due_kicks: PopDueKicksUseCase
    due_reminders: DueRemindersUseCase
