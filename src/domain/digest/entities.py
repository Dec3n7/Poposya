"""Недельный дайджест сервера: сырой срез метрик за неделю (id участников +
числа, без имён и без Discord). Имена резолвит ког, текст (AI или шаблон) —
слой форматирования. Так агрегатор тестируется без Discord и без провайдера."""

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class DigestPerson:
    """Участник среза: id + сопутствующее число (очки / находки)."""

    user_id: int
    metric: int


@dataclass(frozen=True)
class DigestBirthday:
    user_id: int
    in_days: int  # 0 = сегодня


@dataclass(frozen=True)
class WeeklyDigest:
    week_start: date  # включительно
    week_end: date  # включительно (последний полный день недели)
    messages: int
    messages_prev: int  # та же длина окна неделей раньше — для дельты
    voice_hours: float
    voice_hours_prev: float
    members_now: int
    members_delta: int  # чистый прирост участников за неделю (может быть < 0)
    peak_day: date | None  # самый оживлённый день недели по сообщениям
    peak_day_messages: int
    stars: tuple[DigestPerson, ...] = ()  # топ по очкам (всё-время: «на вершине»)
    birthdays: tuple[DigestBirthday, ...] = ()  # ближайшие 7 дней
    top_collector: DigestPerson | None = None
    watched_titles: tuple[str, ...] = field(default_factory=tuple)  # кино за неделю

    @property
    def is_empty(self) -> bool:
        """Нечего рассказывать: пустой сервер/первая неделя — дайджест пропускаем."""
        return not (
            self.messages
            or self.voice_hours
            or self.members_delta
            or self.stars
            or self.birthdays
            or self.watched_titles
        )
