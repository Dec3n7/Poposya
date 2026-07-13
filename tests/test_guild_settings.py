"""GuildSettingsService: реестр, парсинг/валидация, кэш, get/current/override,
set/reset поверх реального SQLite."""
import pytest

from src.config import Settings
from src.infrastructure.guild_settings import (
    SETTING_SPECS,
    GuildSettingsService,
    SettingSpec,
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
