"""Шаг 4b: диалоговые параметры AI считаются пер-серверно.

Реальный GuildSettingsService: сервер 10 с оверрайдом диалоговых настроек
ведёт себя иначе сервера 20 на глобальных дефолтах."""

from datetime import timedelta

import pytest

from src.config import Settings
from src.infrastructure.guild_settings import GuildSettingsService
from tests.test_chat_service import NOW, make_service


def make_settings(**over):
    return Settings(_env_file=None, discord_token="t", **over)


@pytest.fixture
async def gs(session_factory):
    svc = GuildSettingsService(make_settings(), session_factory)
    await svc.load_all()
    return svc


async def test_dialog_gap_per_guild(gs):
    # сервер 10 закрывает диалог уже через 1 минуту паузы; сервер 20 — дефолт 30
    await gs.set(10, "ai_dialog_gap_minutes", "1")
    svc = make_service(settings_provider=gs, dialog_gap_minutes=30, dialog_min_exchanges=3)
    for _ in range(3):
        svc._record_exchange(10, 1, "A", "u", "r", NOW)
        svc._record_exchange(20, 2, "B", "u", "r", NOW)
    later = NOW + timedelta(minutes=2)
    assert svc._pop_stale_session(10, 1, later) is not None  # пауза 1 мин превышена
    assert svc._pop_stale_session(20, 2, later) is None  # дефолтные 30 мин ещё нет


async def test_min_exchanges_per_guild(gs):
    # серверу 10 хватает 1 обмена для резюме; серверу 20 нужно дефолтных 3
    await gs.set(10, "ai_dialog_min_exchanges", "1")
    svc = make_service(settings_provider=gs, dialog_gap_minutes=30, dialog_min_exchanges=3)
    svc._record_exchange(10, 1, "A", "u", "r", NOW)
    svc._record_exchange(20, 2, "B", "u", "r", NOW)
    due_guilds = {g for g, _, _, _ in svc.evict_stale_sessions(NOW + timedelta(minutes=31))}
    assert 10 in due_guilds  # 1 обмен достаточно для сервера 10
    assert 20 not in due_guilds  # серверу 20 одного обмена мало
