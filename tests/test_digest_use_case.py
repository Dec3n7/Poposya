"""BuildWeeklyDigest: агрегатор недельного среза из репозиториев (фейковый UoW,
без БД). Проверяем недельные окна/дельты, фильтры по дате и резолв ДР по guild."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.application.digest.use_cases import BuildWeeklyDigestUseCase

GID = 10
NOW = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)  # воскресенье


def _profile(user_id, points=0, guild_id=GID):
    return SimpleNamespace(user_id=user_id, points=points, guild_id=guild_id)


class _FakeUow:
    def __init__(self):
        prev = [(date(2026, 7, d), 5) for d in (20, 21, 22, 23, 24, 25, 26)]  # 7 дн, сумма 35
        this = [
            (date(2026, 7, 27), 10),
            (date(2026, 7, 28), 10),
            (date(2026, 7, 29), 10),
            (date(2026, 7, 30), 10),
            (date(2026, 7, 31), 10),
            (date(2026, 8, 1), 50),  # пик
            (date(2026, 8, 2), 10),
        ]  # сумма 110
        self.message_activity = SimpleNamespace(
            daily=AsyncMock(return_value=prev + this),
            voice_hourly=AsyncMock(
                return_value=[
                    (date(2026, 7, 21), 10, 7200),  # прошлая неделя: 2 ч
                    (date(2026, 7, 28), 20, 3600),  # эта неделя: 1 ч
                ]
            ),
        )
        self.metrics = SimpleNamespace(
            series=AsyncMock(
                return_value={
                    "members": [
                        (date(2026, 7, 20), 100.0),
                        (date(2026, 7, 26), 102.0),  # последний до недели
                        (date(2026, 8, 2), 105.0),  # сейчас
                    ]
                }
            )
        )
        self.relationships = SimpleNamespace(
            top_by_points=AsyncMock(
                return_value=[_profile(1, 1200), _profile(2, 800), _profile(3, 0)]
            ),
            find_birthdays=self._find_birthdays,
        )
        self.collections = SimpleNamespace(top_collectors=AsyncMock(return_value=[(9, 42, 5)]))
        self.movies = SimpleNamespace(
            list_watched=AsyncMock(
                return_value=[
                    SimpleNamespace(title="Матрица", watched_at=datetime(2026, 7, 28, tzinfo=UTC)),
                    SimpleNamespace(title="Старьё", watched_at=datetime(2026, 7, 1, tzinfo=UTC)),
                    SimpleNamespace(title="БезДаты", watched_at=None),
                ]
            )
        )

    async def _find_birthdays(self, month, day):
        # ДР 4 августа: один в нашей гильдии, один в чужой (должен отсеяться)
        if (month, day) == (8, 4):
            return [_profile(5, guild_id=GID), _profile(6, guild_id=999)]
        return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_build_weekly_digest_aggregates():
    uc = BuildWeeklyDigestUseCase(lambda: _FakeUow())
    d = await uc.execute(GID, NOW)

    assert d.week_start == date(2026, 7, 27) and d.week_end == date(2026, 8, 2)
    assert d.messages == 110 and d.messages_prev == 35
    assert d.voice_hours == 1.0 and d.voice_hours_prev == 2.0
    assert d.members_now == 105 and d.members_delta == 3  # 105 - 102
    assert d.peak_day == date(2026, 8, 1) and d.peak_day_messages == 50
    # звёзды: только с очками > 0
    assert [(p.user_id, p.metric) for p in d.stars] == [(1, 1200), (2, 800)]
    # ДР: только наша гильдия, offset = через сколько дней (4 авг от 2 авг = 2)
    assert [(b.user_id, b.in_days) for b in d.birthdays] == [(5, 2)]
    assert d.top_collector.user_id == 9 and d.top_collector.metric == 42
    # кино: только просмотренное на этой неделе
    assert d.watched_titles == ("Матрица",)
    assert not d.is_empty


async def test_empty_digest_when_no_data():
    class _Empty(_FakeUow):
        def __init__(self):
            super().__init__()
            self.message_activity.daily = AsyncMock(return_value=[])
            self.message_activity.voice_hourly = AsyncMock(return_value=[])
            self.metrics.series = AsyncMock(return_value={})
            self.relationships.top_by_points = AsyncMock(return_value=[])
            self.relationships.find_birthdays = AsyncMock(return_value=[])
            self.collections.top_collectors = AsyncMock(return_value=[])
            self.movies.list_watched = AsyncMock(return_value=[])

    uc = BuildWeeklyDigestUseCase(lambda: _Empty())
    d = await uc.execute(GID, NOW)
    assert d.is_empty
