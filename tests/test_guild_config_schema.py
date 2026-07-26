"""GuildSettings — единый источник правды по пер-серверным настройкам.
Ключевая гарантия: дефолты модели не разъезжаются с глобальным Settings."""

import pytest
from pydantic import ValidationError

from src.application.guild_config.schema import (
    KEY_KINDS,
    SETTING_KEYS,
    GuildSettings,
)
from src.config import Settings


def test_defaults_match_global_settings():
    """Каждый ключ GuildSettings имеет тот же дефолт, что и в Settings —
    иначе .env-база и per-guild модель разойдутся незаметно."""
    mismatched = {}
    for key in SETTING_KEYS:
        guild_default = GuildSettings.model_fields[key].get_default()
        global_default = Settings.model_fields[key].get_default()
        if guild_default != global_default:
            mismatched[key] = (guild_default, global_default)
    assert not mismatched, f"дефолты разъехались: {mismatched}"


def test_every_key_exists_in_global_settings():
    """Нельзя завести per-guild ключ, которого нет в глобальном Settings."""
    unknown = [k for k in SETTING_KEYS if k not in Settings.model_fields]
    assert unknown == []


def test_default_model_is_valid():
    GuildSettings()  # дефолты проходят все валидаторы


def test_kinds_cover_all_keys():
    assert set(KEY_KINDS) == set(SETTING_KEYS)
    assert KEY_KINDS["music_karaoke_ansi"] == "bool"
    assert KEY_KINDS["cinema_forum_channel"] == "channel"
    assert KEY_KINDS["ai_event_comment_chance"] == "float"
    assert KEY_KINDS["relationship_role_names"] == "list"
    assert KEY_KINDS["ai_rate_limits_by_level"] == "dict"
    assert KEY_KINDS["warn_threshold"] == "int"
    # строковое поле (имя роли-новичка) — новый kind «text», не «int»
    assert KEY_KINDS["relationship_newcomer_role"] == "text"
    # ключи-каналы попадают в kind только через CHANNEL_KEYS: забыть их там —
    # значит молча получить "int" и текстовый ввод ID вместо пикера канала
    assert KEY_KINDS["tempvoice_hub_channel"] == "channel"
    assert KEY_KINDS["tempvoice_category"] == "channel"


def test_range_validation():
    with pytest.raises(ValidationError):
        GuildSettings(warn_threshold=0)
    with pytest.raises(ValidationError):
        GuildSettings(ai_event_comment_chance=1.5)


def test_thresholds_must_increase():
    with pytest.raises(ValidationError, match="строго возрастать"):
        GuildSettings(relationship_role_thresholds=[100, 100, 200])


def test_role_names_count_must_match_thresholds():
    # 3 порога -> нужно 4 имени; даём 2 — ошибка
    with pytest.raises(ValidationError, match="имён ролей"):
        GuildSettings(
            relationship_role_thresholds=[100, 200, 300],
            relationship_role_names=["a", "b"],
        )


def test_exclusive_threshold_above_last():
    with pytest.raises(ValidationError, match="эксклюзивный порог"):
        GuildSettings(
            relationship_role_thresholds=[100, 200],
            relationship_exclusive_threshold=150,
            relationship_role_names=["a", "b", "c"],
        )


def test_finds_interval_order():
    with pytest.raises(ValidationError, match="интервал находок"):
        GuildSettings(finds_min_interval_hours=48, finds_max_interval_hours=12)


def test_points_policy_reflects_overrides():
    gs = GuildSettings(
        relationship_role_thresholds=[50, 150],
        relationship_exclusive_threshold=300,
        relationship_role_names=["a", "b", "c"],
    )
    policy = gs.points_policy()
    assert policy.thresholds == (50, 150)
    assert policy.exclusive_threshold == 300
