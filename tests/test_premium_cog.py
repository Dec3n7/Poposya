"""/premium — карточка тарифа (PNG) с фолбэком на эмбед; голос Попоси."""

from datetime import datetime
from unittest.mock import MagicMock

from src.application.interfaces.entitlements import PlanTier
from src.infrastructure.discord.cogs.premium import PremiumCog
from src.infrastructure.render.cards import PREMIUM_H, PREMIUM_W, premium_card_html
from tests.cog_fakes import make_interaction


class _Ent:
    def __init__(self, result):
        self._r = result

    def current(self, guild_id):
        return self._r


class _Renderer:
    def __init__(self, png=b"PNGDATA"):
        self.png = png
        self.calls = 0

    async def render(self, html, w, h):
        self.calls += 1
        self.last = (html, w, h)
        return self.png


def _cog(current, renderer=None):
    return PremiumCog(MagicMock(), MagicMock(), entitlements=_Ent(current), card_renderer=renderer)


# --- билдер HTML-карточки ---


def test_card_html_dimensions_and_highlight():
    html, w, h = premium_card_html("premium")
    assert (w, h) == (PREMIUM_W, PREMIUM_H)
    assert "col premium active" in html
    assert "ТЕКУЩИЙ" in html
    # текущая колонка одна
    assert html.count("ТЕКУЩИЙ") == 1


def test_card_html_unknown_tier_falls_back_to_free():
    html, _, _ = premium_card_html("platinum")
    assert "col free active" in html


# --- команда: карточка (рендерер есть) ---


async def test_premium_sends_card_when_renderer():
    renderer = _Renderer()
    cog = _cog((PlanTier.PREMIUM, None, True), renderer)
    it = make_interaction()
    await type(cog).premium.callback(cog, it)
    it.response.defer.assert_awaited_once()
    kwargs = it.followup.send.await_args.kwargs
    assert kwargs.get("file") is not None  # PNG-карточка
    assert renderer.calls == 1
    assert renderer.last[1:] == (PREMIUM_W, PREMIUM_H)


# --- команда: фолбэк на эмбед (рендерер отсутствует/упал) ---


async def test_premium_embed_fallback_free():
    cog = _cog((PlanTier.FREE, None, False))  # renderer=None
    it = make_interaction()
    await type(cog).premium.callback(cog, it)
    embed = it.followup.send.await_args.kwargs["embed"]
    assert "Free" in embed.description
    assert [f.name for f in embed.fields] == ["Free", "Premium", "Pro"]


async def test_premium_embed_fallback_active_with_expiry():
    exp = datetime(2026, 9, 8, 12, 0)
    cog = _cog((PlanTier.PRO, exp, True))
    it = make_interaction()
    await type(cog).premium.callback(cog, it)
    embed = it.followup.send.await_args.kwargs["embed"]
    assert "Pro" in embed.description and "08.09.2026" in embed.description


async def test_premium_render_error_falls_back_to_embed():
    class _Boom:
        async def render(self, *_):
            raise RuntimeError("no browser")

    cog = _cog((PlanTier.PREMIUM, None, True), _Boom())
    it = make_interaction()
    await type(cog).premium.callback(cog, it)
    # упавший рендер → всё равно ответили эмбедом
    assert it.followup.send.await_args.kwargs.get("embed") is not None
