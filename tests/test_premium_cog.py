"""/premium — показ тарифа сервера и перечня возможностей (голос Попоси)."""

from datetime import datetime
from unittest.mock import MagicMock

from src.application.interfaces.entitlements import PlanTier
from src.infrastructure.discord.cogs.premium import PremiumCog
from tests.cog_fakes import make_interaction


class _Ent:
    def __init__(self, result):
        self._r = result

    def current(self, guild_id):
        return self._r


def _cog(current):
    return PremiumCog(MagicMock(), MagicMock(), entitlements=_Ent(current))


async def _run(cog):
    it = make_interaction()
    await type(cog).premium.callback(cog, it)
    return it


async def test_premium_free_tier():
    it = await _run(_cog((PlanTier.FREE, None, False)))
    embed = it.response.send_message.await_args.kwargs["embed"]
    assert it.response.send_message.await_args.kwargs.get("ephemeral") is True
    assert "Free" in embed.description
    # три поля с уровнями всегда присутствуют
    assert [f.name for f in embed.fields] == ["Free", "Premium", "Pro"]


async def test_premium_active_with_expiry():
    exp = datetime(2026, 9, 8, 12, 0)
    it = await _run(_cog((PlanTier.PREMIUM, exp, True)))
    embed = it.response.send_message.await_args.kwargs["embed"]
    assert "Premium" in embed.description
    assert "08.09.2026" in embed.description


async def test_premium_active_permanent():
    it = await _run(_cog((PlanTier.PRO, None, True)))
    embed = it.response.send_message.await_args.kwargs["embed"]
    assert "бессрочно" in embed.description


async def test_premium_without_entitlements():
    cog = PremiumCog(MagicMock(), MagicMock(), entitlements=None)
    it = make_interaction()
    await type(cog).premium.callback(cog, it)
    assert it.response.send_message.await_args.kwargs["embed"] is not None
