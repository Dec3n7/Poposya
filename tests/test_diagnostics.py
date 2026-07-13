"""Диагностика старта: сводка конфигурации (что вкл/выкл) и проверка связи
с внешними сервисами через фейковый aiohttp — без сети."""

import logging

import aiohttp
import pytest

from src.config import Settings
from src.infrastructure.diagnostics import (
    ProbeResult,
    _db_label,
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
