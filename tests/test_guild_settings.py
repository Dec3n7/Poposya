"""GuildSettingsService: реестр, парсинг/валидация, кэш, get/current/override,
set/reset поверх реального SQLite."""

import pytest

from src.config import Settings
from src.infrastructure.guild_settings import (
    SETTING_SPECS,
    GuildSettingsService,
)


def make_settings(**over):
    return Settings(_env_file=None, discord_token="t", **over)


@pytest.fixture
def service(session_factory):
    return GuildSettingsService(make_settings(), session_factory)


# --- реестр / парсинг -------------------------------------------------------


def test_registry_keys_are_settings_attrs():
    s = make_settings()
    for key in SETTING_SPECS:
        assert hasattr(s, key), f"нет атрибута Settings.{key}"


def test_parse_int_range():
    spec = SETTING_SPECS["warn_threshold"]  # 1..20
    assert spec.parse(" 3 ") == 3
    with pytest.raises(ValueError, match="минимум"):
        spec.parse("0")
    with pytest.raises(ValueError, match="максимум"):
        spec.parse("99")
    with pytest.raises(ValueError, match="целое"):
        spec.parse("абв")


def test_parse_channel_accepts_mention_id_zero():
    spec = SETTING_SPECS["cinema_forum_channel"]
    assert spec.parse("<#12345>") == 12345
    assert spec.parse("67890") == 67890
    assert spec.parse("0") == 0
    with pytest.raises(ValueError):
        spec.parse("не-канал")


# --- get / current / default ------------------------------------------------


async def test_get_returns_default_without_override(service):
    await service.load_all()
    # дефолт из Settings
    assert service.current(10, "warn_threshold") == make_settings().warn_threshold
    assert service.is_override(10, "warn_threshold") is False
    # get с явным дефолтом
    assert service.get(10, "warn_threshold", 99) == 99


async def test_set_overrides_and_persists(service, session_factory):
    await service.load_all()
    value = await service.set(10, "warn_threshold", "5")
    assert value == 5
    assert service.current(10, "warn_threshold") == 5
    assert service.is_override(10, "warn_threshold") is True
    assert service.get(10, "warn_threshold", 99) == 5

    # переживает перезагрузку кэша (лежит в БД)
    fresh = GuildSettingsService(make_settings(), session_factory)
    await fresh.load_all()
    assert fresh.current(10, "warn_threshold") == 5


async def test_set_is_per_guild(service):
    await service.load_all()
    await service.set(10, "spam_limit", "7")
    assert service.current(10, "spam_limit") == 7
    # другой сервер не затронут
    assert service.current(20, "spam_limit") == make_settings().spam_limit


async def test_set_validation_rejects_bad_value(service):
    await service.load_all()
    with pytest.raises(ValueError):
        await service.set(10, "warn_threshold", "999")
    # ничего не сохранилось
    assert service.is_override(10, "warn_threshold") is False


async def test_reset_returns_to_default(service):
    await service.load_all()
    await service.set(10, "lonely_hours", "6")
    assert service.current(10, "lonely_hours") == 6
    assert await service.reset(10, "lonely_hours") is True
    assert service.is_override(10, "lonely_hours") is False
    assert service.current(10, "lonely_hours") == make_settings().lonely_hours
    # повторный reset — уже нечего
    assert await service.reset(10, "lonely_hours") is False


async def test_overrides_snapshot(service):
    await service.load_all()
    await service.set(10, "spam_limit", "8")
    await service.set(10, "voice_points_per_hour", "5")
    ov = service.overrides(10)
    assert ov == {"spam_limit": 8, "voice_points_per_hour": 5}


async def test_channel_setting_roundtrip(service):
    await service.load_all()
    await service.set(10, "cinema_forum_channel", "<#555>")
    assert service.current(10, "cinema_forum_channel") == 555


# --- модель: float, resolved, кросс-полевые инварианты ----------------------


async def test_set_float_setting(service):
    await service.load_all()
    value = await service.set(10, "ai_event_comment_chance", "0,25")  # запятая тоже ок
    assert value == 0.25
    assert service.current(10, "ai_event_comment_chance") == 0.25
    with pytest.raises(ValueError, match="максимум"):
        await service.set(10, "ai_event_comment_chance", "1.5")


async def test_resolved_merges_defaults_and_overrides(service):
    await service.load_all()
    await service.set(10, "warn_threshold", "7")
    gs = service.resolved(10)
    assert gs.warn_threshold == 7  # переопределение
    assert gs.spam_limit == make_settings().spam_limit  # дефолт из Settings
    # другой сервер — чистые дефолты
    assert service.resolved(20).warn_threshold == make_settings().warn_threshold


async def test_resolved_points_policy_per_guild(service):
    await service.load_all()
    await service.set(10, "relationship_exclusive_threshold", "2000")
    assert service.resolved(10).points_policy().exclusive_threshold == 2000


async def test_set_rejects_cross_field_invariant(service):
    await service.load_all()
    # эксклюзивный порог ниже последнего порога ролей (1200) — инвариант модели
    with pytest.raises(ValueError, match="эксклюзивный порог"):
        await service.set(10, "relationship_exclusive_threshold", "1000")
    assert service.is_override(10, "relationship_exclusive_threshold") is False
    # интервал находок: max < min
    with pytest.raises(ValueError, match="интервал находок"):
        await service.set(10, "finds_max_interval_hours", "6")


async def test_resolved_memoized_until_write(service):
    await service.load_all()
    first = service.resolved(10)
    assert service.resolved(10) is first  # тот же объект (мемоизация)
    await service.set(10, "warn_threshold", "9")
    assert service.resolved(10) is not first  # сброшено после записи


# --- set_many: списки/словари ----------------------------------------------


async def test_set_many_lists_roundtrip(service, session_factory):
    await service.load_all()
    await service.set_many(
        10,
        {
            "relationship_role_thresholds": [50, 150],
            "relationship_role_names": ["a", "b", "c"],
        },
    )
    assert service.resolved(10).points_policy().thresholds == (50, 150)
    assert service.resolved(10).relationship_role_names == ["a", "b", "c"]
    # переживает перезагрузку кэша (лежит в БД как JSON)
    fresh = GuildSettingsService(make_settings(), session_factory)
    await fresh.load_all()
    assert fresh.resolved(10).points_policy().thresholds == (50, 150)
    assert fresh.resolved(10).relationship_role_names == ["a", "b", "c"]


async def test_set_many_dict_roundtrip(service, session_factory):
    await service.load_all()
    limits = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7}
    await service.set_many(10, {"ai_rate_limits_by_level": limits})
    fresh = GuildSettingsService(make_settings(), session_factory)
    await fresh.load_all()
    assert fresh.resolved(10).ai_rate_limits_by_level == limits


async def test_set_many_atomic_reject_on_invariant(service):
    await service.load_all()
    # 2 порога требуют 3 имени; даём 2 — вся операция откатывается
    with pytest.raises(ValueError, match="имён ролей"):
        await service.set_many(
            10,
            {
                "relationship_role_thresholds": [50, 150],
                "relationship_role_names": ["a", "b"],
            },
        )
    assert service.is_override(10, "relationship_role_thresholds") is False
    assert service.is_override(10, "relationship_role_names") is False


# --- reload_guild: межпроцессная инвалидация кэша (что дёргает NOTIFY-листенер) ---


async def test_reload_guild_picks_up_foreign_write(session_factory):
    """Два процесса на одной БД: «панель» (writer) пишет настройку, «бот»
    (reader) держит устаревший кэш, пока не перечитает гильдию. Ровно это делает
    SettingsChangeListener, получив NOTIFY."""
    bot = GuildSettingsService(make_settings(), session_factory)
    panel = GuildSettingsService(make_settings(), session_factory)
    await bot.load_all()
    await panel.load_all()

    await panel.set(10, "warn_threshold", "9")
    assert bot.current(10, "warn_threshold") == make_settings().warn_threshold  # ещё старое

    await bot.reload_guild(10)
    assert bot.current(10, "warn_threshold") == 9  # увидел чужую запись
    assert bot.resolved(10).warn_threshold == 9  # мемоизация тоже сброшена


async def test_reload_guild_picks_up_foreign_reset(session_factory):
    """reset в другом процессе тоже виден после reload: переопределение снимается,
    возвращается дефолт."""
    bot = GuildSettingsService(make_settings(), session_factory)
    panel = GuildSettingsService(make_settings(), session_factory)
    await panel.set(10, "lonely_hours", "6")
    await bot.reload_guild(10)
    assert bot.current(10, "lonely_hours") == 6

    await panel.reset(10, "lonely_hours")
    assert bot.current(10, "lonely_hours") == 6  # кэш ещё держит старое
    await bot.reload_guild(10)
    assert bot.is_override(10, "lonely_hours") is False  # снято
    assert bot.current(10, "lonely_hours") == make_settings().lonely_hours


async def test_reload_guild_isolated_per_guild(session_factory):
    """reload одной гильдии не трогает кэш другой."""
    bot = GuildSettingsService(make_settings(), session_factory)
    panel = GuildSettingsService(make_settings(), session_factory)
    await panel.set(10, "spam_limit", "7")
    await panel.set(20, "spam_limit", "8")
    await bot.reload_guild(10)
    assert bot.current(10, "spam_limit") == 7
    assert bot.is_override(20, "spam_limit") is False  # гильдию 20 не перечитывали


# --- фабрика листенера: только Postgres, разбор DSN ------------------------


def test_settings_listener_none_on_sqlite():
    from src.infrastructure.settings_listener import make_settings_listener

    # SQLite — один писатель, межпроцессная инвалидация не нужна -> листенера нет
    assert make_settings_listener("sqlite+aiosqlite:///x.db", object()) is None


def test_asyncpg_dsn_strips_sqlalchemy_driver():
    from src.infrastructure.settings_listener import _asyncpg_dsn

    # asyncpg.connect не понимает суффикс +asyncpg
    assert _asyncpg_dsn("postgresql+asyncpg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
