"""Шаг 4a: настройки отношений считаются пер-серверно.

Реальный GuildSettingsService: переопределение на сервере 10 меняет поведение
use-case'ов, сервер 20 остаётся на глобальных дефолтах."""

from datetime import UTC, datetime

import pytest

from src.application.relationship.use_cases import (
    AwardPointUseCase,
    CompleteSurveyUseCase,
    GetRankUseCase,
    SetPointsUseCase,
    UpdateUserNotesUseCase,
)
from src.config import Settings
from src.domain.relationship.policies import PointsToLevelPolicy
from src.infrastructure.guild_settings import GuildSettingsService

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
POLICY = PointsToLevelPolicy()


def make_settings(**over):
    return Settings(_env_file=None, discord_token="t", **over)


@pytest.fixture
async def gs(session_factory):
    svc = GuildSettingsService(make_settings(), session_factory)
    await svc.load_all()
    return svc


async def test_exclusive_threshold_per_guild(gs, uow_factory):
    # сервер 10 поднимает порог «Единственного»; глобальный дефолт — 1250
    await gs.set(10, "relationship_exclusive_threshold", "2000")
    setter = SetPointsUseCase(uow_factory, POLICY, settings_provider=gs)
    r10 = await setter.execute(1, 10, 1300)  # 1300 < 2000 -> ещё не эксклюзив
    r20 = await setter.execute(2, 20, 1300)  # 1300 >= 1250 -> эксклюзив
    assert r10.is_exclusive is False
    assert r20.is_exclusive is True


async def test_survey_bonus_per_guild(gs, uow_factory):
    await gs.set(10, "survey_bonus_points", "50")
    survey = CompleteSurveyUseCase(uow_factory, POLICY, bonus=5, settings_provider=gs)
    r10 = await survey.execute(1, 10, NOW)
    r20 = await survey.execute(2, 20, NOW)
    assert r10.bonus_awarded == 50
    assert r20.bonus_awarded == 5


async def test_notes_limit_per_guild(gs, uow_factory):
    await gs.set(10, "relationship_notes_max_chars", "100")
    notes = UpdateUserNotesUseCase(uow_factory, 700, settings_provider=gs)
    rank = GetRankUseCase(uow_factory, POLICY, settings_provider=gs)
    await notes.execute(1, 10, "x" * 500)
    await notes.execute(2, 20, "x" * 500)
    assert len((await rank.execute(1, 10)).user_notes) == 100  # обрезано по серверу 10
    assert len((await rank.execute(2, 20)).user_notes) == 500  # дефолт 700 — не тронуто


async def test_daily_cap_per_guild(gs, uow_factory):
    await gs.set(10, "relationship_daily_point_cap", "2")
    award = AwardPointUseCase(
        uow_factory, POLICY, daily_cap=20, absence_days=30, settings_provider=gs
    )
    for _ in range(5):
        await award.execute(1, 10, 0, NOW)
    rank = await GetRankUseCase(uow_factory, POLICY, settings_provider=gs).execute(1, 10)
    assert rank.points == 2  # потолок сервера 10 — 2 очка/день
