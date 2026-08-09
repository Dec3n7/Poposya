"""require_tier — гейт модуля по тарифу (подготовка к монетизации).

Проверяем: no-op при заглушке PRO и при неподключённом провайдере; отказ с
эфемерным сообщением, когда тариф ниже требуемого; free-модули пропускаются."""

import pytest

from src.application.interfaces.entitlements import IEntitlements, PlanTier
from src.infrastructure.discord.feature_flags import require_tier
from src.infrastructure.entitlements import UnlimitedEntitlements


class _Response:
    def __init__(self):
        self.messages: list[tuple[str, bool]] = []

    async def send_message(self, content, ephemeral=False):
        self.messages.append((content, ephemeral))


class _Interaction:
    def __init__(self, guild_id=42):
        self.guild_id = guild_id
        self.response = _Response()


class _FixedTier(IEntitlements):
    def __init__(self, tier: PlanTier):
        self._tier = tier

    def tier(self, guild_id: int) -> PlanTier:
        return self._tier


async def _run(interaction, entitlements, key):
    return await require_tier(interaction, entitlements, key)


@pytest.mark.asyncio
async def test_stub_pro_allows_premium_module():
    it = _Interaction()
    assert await _run(it, UnlimitedEntitlements(), "git_enabled") is True
    assert it.response.messages == []


@pytest.mark.asyncio
async def test_none_provider_allows():
    it = _Interaction()
    assert await _run(it, None, "staykick_enabled") is True
    assert it.response.messages == []


@pytest.mark.asyncio
async def test_free_module_always_allows():
    it = _Interaction()
    # moderation_enabled нет в карте тарифов -> free
    assert await _run(it, _FixedTier(PlanTier.FREE), "moderation_enabled") is True
    assert it.response.messages == []


@pytest.mark.asyncio
async def test_free_tier_denied_on_premium_module():
    it = _Interaction()
    assert await _run(it, _FixedTier(PlanTier.FREE), "git_enabled") is False
    assert len(it.response.messages) == 1
    assert it.response.messages[0][1] is True  # ephemeral


@pytest.mark.asyncio
async def test_premium_tier_denied_on_pro_module():
    it = _Interaction()
    assert await _run(it, _FixedTier(PlanTier.PREMIUM), "staykick_enabled") is False


@pytest.mark.asyncio
async def test_premium_tier_allows_premium_module():
    it = _Interaction()
    assert await _run(it, _FixedTier(PlanTier.PREMIUM), "git_enabled") is True
