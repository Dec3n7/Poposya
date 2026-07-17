"""Диагностика старта: сводка конфигурации (что вкл/выкл) и проверка связи
с внешними сервисами через фейковый aiohttp — без сети."""

import logging

import aiohttp

from src.config import Settings
from src.infrastructure.diagnostics import (
    _db_label,
    backup_status,
    log_boot_summary,
    probe_dependencies,
)
from tests.aiohttp_fakes import FakeResponse, FakeSession


def make_settings(**over):
    base = dict(discord_token="t")
    base.update(over)
    return Settings(_env_file=None, **base)


# --- _db_label --------------------------------------------------------------


def test_db_label_sqlite_shows_path():
    assert _db_label("sqlite+aiosqlite:////app/data/poposya.db") == "/app/data/poposya.db"


def test_db_label_hides_postgres_credentials():
    label = _db_label("postgresql+asyncpg://user:secret@host/db")
    assert "secret" not in label
    assert label.startswith("postgresql+asyncpg://")


# --- бэкап: молчаливое отсутствие — худший сюрприз ---------------------------


def test_backup_status_ok_for_sqlite():
    assert backup_status(make_settings(database_url="sqlite+aiosqlite:///./p.db")) is None


def test_backup_status_ok_for_postgres():
    # Postgres теперь бэкапится через pg_dump — не повод для предупреждения
    assert backup_status(make_settings(database_url="postgresql+asyncpg://u:p@h/d")) is None


def test_backup_status_explains_unknown_db():
    reason = backup_status(make_settings(database_url="mysql://u:p@h/d"))
    assert reason is not None and "SQLite" in reason and "PostgreSQL" in reason


def test_backup_status_explains_zero_settings():
    for over in ({"backup_interval_hours": 0}, {"backup_keep": 0}):
        reason = backup_status(make_settings(database_url="sqlite+aiosqlite:///./p.db", **over))
        assert reason is not None and "BACKUP_" in reason


def test_boot_summary_warns_when_backup_disabled(caplog):
    """Бэкап выключен настройками: сводка обязана сказать об этом громко,
    а не обещать несуществующие копии."""
    settings = make_settings(
        database_url="postgresql+asyncpg://u:secret@h/d", backup_interval_hours=0
    )
    with caplog.at_level(logging.INFO, logger="boot"):
        log_boot_summary(settings)
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("АВТОБЭКАПА НЕТ" in m for m in warnings)
    assert not any("бэкап каждые" in r.getMessage() for r in caplog.records)  # не врём
    assert not any("secret" in r.getMessage() for r in caplog.records)  # пароль не светим


def test_boot_summary_reports_backup_for_postgres(caplog):
    # с включёнными настройками Postgres рапортует про бэкап, а не про его отсутствие
    settings = make_settings(database_url="postgresql+asyncpg://u:secret@h/d")
    with caplog.at_level(logging.INFO, logger="boot"):
        log_boot_summary(settings)
    text = "\n".join(caplog.messages)
    assert "бэкап каждые" in text
    assert "АВТОБЭКАПА НЕТ" not in text
    assert "secret" not in text  # пароль не светим и в норме


# --- log_boot_summary -------------------------------------------------------


def test_boot_summary_ai_on(caplog):
    settings = make_settings(groq_api_key="k", ai_model="m1", ai_fallback_model="m2")
    with caplog.at_level(logging.INFO, logger="boot"):
        log_boot_summary(settings)
    text = "\n".join(caplog.messages)
    assert "AI       : ВКЛ" in text
    assert "m1" in text and "m2" in text


def test_boot_summary_ai_off_explains_why(caplog):
    settings = make_settings(groq_api_key="")
    with caplog.at_level(logging.INFO, logger="boot"):
        log_boot_summary(settings)
    text = "\n".join(caplog.messages)
    assert "AI       : ВЫКЛ" in text
    assert "GROQ_API_KEY не задан" in text


def test_boot_summary_reports_toggles(caplog):
    settings = make_settings(
        groq_api_key="",
        voice_points_per_hour=0,
        music_prefetch_tracks=0,
        finds_min_interval_hours=4,
        finds_max_interval_hours=6,
        movie_provider="kinopoisk",
        kinopoisk_api_key="kp",
        tmdb_api_key="",
    )
    with caplog.at_level(logging.INFO, logger="boot"):
        log_boot_summary(settings)
    text = "\n".join(caplog.messages)
    assert "Войс     : очки ВЫКЛ" in text
    assert "аудио-кэш ВЫКЛ" in text
    assert "интервал 4–6 ч" in text
    assert "provider=kinopoisk" in text
    assert "kinopoisk:есть" in text and "tmdb:нет" in text


def test_boot_summary_masks_db_password(caplog):
    settings = make_settings(database_url="postgresql+asyncpg://u:p@h/db")
    with caplog.at_level(logging.INFO, logger="boot"):
        log_boot_summary(settings)
    assert "p@h" not in "\n".join(caplog.messages)


# --- probe_dependencies -----------------------------------------------------


def ok_factory():
    return lambda: FakeSession(response=FakeResponse(200))


async def test_probe_all_ok(caplog):
    settings = make_settings(groq_api_key="g", tmdb_api_key="t", kinopoisk_api_key="k")
    with caplog.at_level(logging.INFO, logger="boot"):
        results = await probe_dependencies(settings, session_factory=ok_factory())
    assert len(results) == 3
    assert all(r.ok for r in results)
    assert "Groq (AI) — ОК" in "\n".join(caplog.messages)


async def test_probe_bad_key_reported(caplog):
    settings = make_settings(groq_api_key="g")
    factory = lambda: FakeSession(response=FakeResponse(401))
    with caplog.at_level(logging.WARNING, logger="boot"):
        results = await probe_dependencies(settings, session_factory=factory)
    assert results[0].ok is False
    assert "ключ неверный" in results[0].detail
    assert "НЕ УДАЛОСЬ" in "\n".join(caplog.messages)


async def test_probe_network_failure_reported():
    settings = make_settings(groq_api_key="g")
    factory = lambda: FakeSession(exc=aiohttp.ClientError("connection refused"))
    results = await probe_dependencies(settings, session_factory=factory)
    assert results[0].ok is False
    assert "нет связи" in results[0].detail


async def test_probe_server_error_reported():
    settings = make_settings(kinopoisk_api_key="k")
    factory = lambda: FakeSession(response=FakeResponse(503))
    results = await probe_dependencies(settings, session_factory=factory)
    assert results[0].ok is False
    assert "HTTP 503" in results[0].detail


async def test_probe_skips_when_nothing_configured(caplog):
    settings = make_settings(groq_api_key="", tmdb_api_key="", kinopoisk_api_key="")
    with caplog.at_level(logging.INFO, logger="boot"):
        results = await probe_dependencies(settings, session_factory=ok_factory())
    assert results == []
    assert "внешних сервисов не настроено" in "\n".join(caplog.messages)


async def test_probe_only_configured_services():
    # только TMDB задан -> одна проверка
    settings = make_settings(groq_api_key="", tmdb_api_key="t", kinopoisk_api_key="")
    results = await probe_dependencies(settings, session_factory=ok_factory())
    assert [r.name for r in results] == ["TMDB"]
