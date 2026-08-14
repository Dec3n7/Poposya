"""PremiumCog.activate: активация ключа из Discord (голос Попоси, ephemeral).

Проверяем сообщения по исходам и что команда зовёт redeem и отвечает эфемерно.
Гейт Manage Guild навешен декоратором app_commands — доверяем discord.py; здесь
дёргаем тело команды напрямую (callback).
"""

from datetime import datetime

from src.application.interfaces.entitlements import PlanTier
from src.config import Settings
from src.infrastructure.discord.cogs.premium import PremiumCog
from src.infrastructure.entitlements import EntitlementService
from src.infrastructure.premium_keys.service import (
    PremiumKeyService,
    RedeemOutcome,
    RedeemResult,
)
from tests.cog_fakes import make_interaction

SECRET = "cog-signing-secret-not-for-production-0123456789"


def _cog(premium_keys=None):
    settings = Settings(_env_file=None, discord_token="t")
    return PremiumCog(bot=None, settings=settings, premium_keys=premium_keys)


def test_redeem_messages_cover_all_outcomes():
    cog = _cog()
    exp = datetime(2027, 1, 1)
    assert "Premium" in cog._redeem_message(
        RedeemResult(RedeemOutcome.OK, "", tier=PlanTier.PREMIUM, expires_at=exp)
    )
    assert "Продлила" in cog._redeem_message(
        RedeemResult(RedeemOutcome.EXTENDED, "", tier=PlanTier.PRO, expires_at=exp)
    )
    assert "заняты" in cog._redeem_message(RedeemResult(RedeemOutcome.FULL, "", seats_total=5))
    assert "отозвали" in cog._redeem_message(RedeemResult(RedeemOutcome.REVOKED, ""))
    assert "просрочен" in cog._redeem_message(RedeemResult(RedeemOutcome.EXPIRED, ""))
    assert "час" in cog._redeem_message(RedeemResult(RedeemOutcome.RATE_LIMITED, ""))
    assert "опечатка" in cog._redeem_message(RedeemResult(RedeemOutcome.INVALID, ""))


async def test_activate_disabled_without_service():
    cog = _cog(premium_keys=None)
    interaction = make_interaction(user_id=7, guild_id=500)
    await cog.activate.callback(cog, interaction, key="POPO-X-Y")
    interaction.followup.send.assert_awaited_once()
    assert "недоступна" in interaction.followup.send.call_args.args[0]


async def test_activate_redeems_and_replies_ephemeral(session_factory):
    ent = EntitlementService(Settings(_env_file=None, discord_token="t"), session_factory)
    svc = PremiumKeyService(session_factory, ent, SECRET)
    batch = await svc.mint_batch(
        tier=PlanTier.PREMIUM, duration_days=30, count=1, label="b", created_by=1
    )
    cog = _cog(premium_keys=svc)
    interaction = make_interaction(user_id=7, guild_id=500)
    await cog.activate.callback(cog, interaction, key=batch.keys[0])

    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.call_args.kwargs
    assert kwargs.get("ephemeral") is True
    assert ent.tier(500) is PlanTier.PREMIUM  # активация реально выдала Premium
