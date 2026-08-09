"""Гейт Premium-модулей по тарифу: слэш-команды режутся на free (через
interaction_check → require_tier), событийные модули (staykick=Pro, альбом=
Premium) — через tier_allows."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.application.interfaces.entitlements import IEntitlements, PlanTier
from src.infrastructure.discord.cogs.secret_room import SecretRoomCog
from src.infrastructure.discord.feature_flags import tier_allows
from src.infrastructure.events.in_memory_bus import InMemoryEventBus
from tests.cog_fakes import make_interaction


class _Tier(IEntitlements):
    def __init__(self, tier: PlanTier):
        self._t = tier

    def tier(self, guild_id: int) -> PlanTier:
        return self._t


def _secret_cog(entitlements):
    # SecretRoomCog — Premium-модуль (secret_room_enabled). Настройки минимальны:
    # module-off гейт видит отсутствие ключа как «включено» (getattr default True).
    settings = SimpleNamespace(
        secret_room_min_level=5,
        secret_room_text_name="t",
        secret_room_voice_name="T",
        relationship_role_names=["R0", "R1", "R2", "R3", "R4", "R5", "R6"],
    )
    return SecretRoomCog(
        MagicMock(), SimpleNamespace(), settings, InMemoryEventBus(), entitlements=entitlements
    )


# --- команды: interaction_check ---


async def test_premium_command_denied_on_free():
    cog = _secret_cog(_Tier(PlanTier.FREE))
    it = make_interaction()
    assert await cog.interaction_check(it) is False
    it.response.send_message.assert_awaited_once()
    assert it.response.send_message.await_args.kwargs.get("ephemeral") is True


async def test_premium_command_allowed_on_premium():
    cog = _secret_cog(_Tier(PlanTier.PREMIUM))
    it = make_interaction()
    assert await cog.interaction_check(it) is True
    it.response.send_message.assert_not_awaited()


async def test_premium_command_allowed_without_entitlements():
    # тесты/окружения без провайдера тарифов не должны ломаться
    cog = _secret_cog(None)
    it = make_interaction()
    assert await cog.interaction_check(it) is True


# --- события: tier_allows ---


def test_tier_allows_event_gate():
    assert tier_allows(None, 10, "staykick_enabled") is True  # нет провайдера
    assert tier_allows(_Tier(PlanTier.FREE), 10, "moderation_enabled") is True  # free-модуль
    # staykick = Pro
    assert tier_allows(_Tier(PlanTier.PREMIUM), 10, "staykick_enabled") is False
    assert tier_allows(_Tier(PlanTier.PRO), 10, "staykick_enabled") is True
    # альбом = Premium
    assert tier_allows(_Tier(PlanTier.FREE), 10, "activity_album") is False
    assert tier_allows(_Tier(PlanTier.PREMIUM), 10, "activity_album") is True
