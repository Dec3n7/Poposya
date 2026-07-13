from dataclasses import dataclass

from src.application.activity.use_cases import (
    AddReminderUseCase,
    GetVoiceHoursUseCase,
    LoadVoiceProgressUseCase,
    PopDueRemindersUseCase,
    SaveVoiceProgressUseCase,
    TouchMemberActivityUseCase,
    TryMarkAlbumPostUseCase,
)


@dataclass(frozen=True)
class ActivityContainer:
    touch_activity: TouchMemberActivityUseCase
    add_reminder: AddReminderUseCase
    pop_due_reminders: PopDueRemindersUseCase
    try_mark_album: TryMarkAlbumPostUseCase
    load_voice_progress: LoadVoiceProgressUseCase
    save_voice_progress: SaveVoiceProgressUseCase
    get_voice_hours: GetVoiceHoursUseCase
