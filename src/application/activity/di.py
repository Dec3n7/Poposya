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
from src.application.message_activity.use_cases import RecordMessageActivityUseCase
from src.application.metrics.use_cases import RecordDailySnapshotUseCase


@dataclass(frozen=True)
class ActivityContainer:
    touch_activity: TouchMemberActivityUseCase
    add_reminder: AddReminderUseCase
    pop_due_reminders: PopDueRemindersUseCase
    try_mark_album: TryMarkAlbumPostUseCase
    load_voice_progress: LoadVoiceProgressUseCase
    save_voice_progress: SaveVoiceProgressUseCase
    get_voice_hours: GetVoiceHoursUseCase
    # суточный снапшот метрик сервера — фундамент трендов на панели
    record_snapshot: RecordDailySnapshotUseCase
    # почасовой учёт сообщений — хитмап и «сообщения/день» на панели
    record_message_activity: RecordMessageActivityUseCase
