from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TempChannel:
    """Временный голосовой канал («каморка»), созданный по входу в канал-хаб.

    Живёт, пока в нём есть хоть один человек; владелец задаётся при создании
    и меняется только кнопкой «Забрать», когда прежнего в канале уже нет."""

    guild_id: int
    channel_id: int
    owner_id: int
    created_at: datetime
