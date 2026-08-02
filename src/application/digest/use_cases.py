"""Сборка недельного среза сервера из существующих репозиториев (одна
транзакция). Возвращает сырой WeeklyDigest (id + числа) — имена резолвит ког,
текст пишет слой форматирования. Всё, что запрашивается, уже копится: суточные
снапшоты, счётчики сообщений/войса, очки, ДР, находки, кино — миграций нет."""

from collections.abc import Callable
from datetime import datetime, timedelta

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.digest.entities import DigestBirthday, DigestPerson, WeeklyDigest

UowFactory = Callable[[], IUnitOfWork]

_WINDOW = 7  # дней в неделе среза
_STARS = 3  # сколько «звёзд» показать
_WATCHED_CAP = 5  # максимум фильмов недели в дайджесте


def _members_delta(series: list[tuple], week_start) -> tuple[int, int]:
    """(участников сейчас, прирост за неделю) из серии снапшотов метрики members.
    Прирост = текущее минус значение на начало недели (последний снапшот до неё)."""
    if not series:
        return 0, 0
    now_val = int(series[-1][1])
    prior = None
    for day, value in series:  # старые→новые
        if day < week_start:
            prior = value
    if prior is None:
        prior = series[0][1]
    return now_val, int(now_val - prior)


class BuildWeeklyDigestUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, now: datetime) -> WeeklyDigest:
        today = now.date()
        week_start = today - timedelta(days=_WINDOW - 1)  # 7 дней включая сегодня
        prev_start = today - timedelta(days=_WINDOW * 2 - 1)  # предыдущие 7 — для дельт

        async with self._uow_factory() as uow:
            # --- сообщения: этой недели и прошлой (для дельты) + пик-день ---
            daily = await uow.message_activity.daily(guild_id, prev_start)
            msgs_this = sum(c for d, c in daily if d >= week_start)
            msgs_prev = sum(c for d, c in daily if d < week_start)
            week_days = [(d, c) for d, c in daily if d >= week_start]
            peak_day, peak_msgs = max(week_days, key=lambda x: x[1], default=(None, 0))

            # --- голос: секунды по (день, час) -> часы ---
            voice = await uow.message_activity.voice_hourly(guild_id, prev_start)
            sec_this = sum(s for d, _h, s in voice if d >= week_start)
            sec_prev = sum(s for d, _h, s in voice if d < week_start)

            # --- участники: из суточных снапшотов метрики members ---
            series = await uow.metrics.series(guild_id, prev_start)
            members_now, members_delta = _members_delta(series.get("members", []), week_start)

            # --- звёзды: топ по очкам (всё-время — «на вершине сейчас») ---
            top = await uow.relationships.top_by_points(guild_id, _STARS)
            stars = tuple(DigestPerson(p.user_id, p.points) for p in top if p.points > 0)

            # --- дни рождения на ближайшие 7 дней (find_birthdays без guild — фильтруем) ---
            birthdays: list[DigestBirthday] = []
            for offset in range(_WINDOW):
                day = today + timedelta(days=offset)
                for prof in await uow.relationships.find_birthdays(day.month, day.day):
                    if prof.guild_id == guild_id:
                        birthdays.append(DigestBirthday(prof.user_id, offset))

            # --- коллекционер недели (топ по находкам, всё-время) ---
            collectors = await uow.collections.top_collectors(guild_id, 1)
            top_collector = (
                DigestPerson(collectors[0][0], collectors[0][1]) if collectors else None
            )

            # --- кино: что попало в золотой фонд за эту неделю ---
            watched = await uow.movies.list_watched(guild_id)
            watched_titles = tuple(
                m.title
                for m in watched
                if m.watched_at is not None and m.watched_at.date() >= week_start
            )[:_WATCHED_CAP]

        return WeeklyDigest(
            week_start=week_start,
            week_end=today,
            messages=msgs_this,
            messages_prev=msgs_prev,
            voice_hours=sec_this / 3600,
            voice_hours_prev=sec_prev / 3600,
            members_now=members_now,
            members_delta=members_delta,
            peak_day=peak_day,
            peak_day_messages=peak_msgs,
            stars=stars,
            birthdays=tuple(birthdays),
            top_collector=top_collector,
            watched_titles=watched_titles,
        )
