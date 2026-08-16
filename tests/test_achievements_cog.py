"""AchievementsCog: реакция на события (карточка в канал) и витрина /achievements.
Контейнер/рендерер/канал моканы — проверяем оркестровку, не рендер и не БД."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import src.infrastructure.discord.cogs.achievements as ach_mod
from src.application.achievements.use_cases import EvalResult, ShowcaseResult
from src.domain.achievements.catalog import BY_ID
from src.domain.achievements.entities import UserStats
from src.domain.finds.events import FindClaimed
from src.infrastructure.discord.cogs.achievements import AchievementsCog
from tests.cog_fakes import make_interaction


def make_cog(eval_result=None, unlocked_ids=None, gs=None, renderer=None, entitlements=None):
    bot = MagicMock()
    ach = SimpleNamespace(
        evaluate=SimpleNamespace(
            execute=AsyncMock(return_value=eval_result or EvalResult([], UserStats()))
        ),
        get=SimpleNamespace(
            execute=AsyncMock(return_value=ShowcaseResult(unlocked_ids or set(), UserStats()))
        ),
    )
    settings = SimpleNamespace(main_channel="основной", achievements_enabled=True)
    renderer = renderer or MagicMock(render=AsyncMock(return_value=b"\x89PNG"))
    cog = AchievementsCog(
        bot, ach, settings, MagicMock(), renderer, gs, persona=None, entitlements=entitlements
    )
    return cog, bot, ach


def _guild_with_channel(monkeypatch):
    channel = MagicMock()
    channel.send = AsyncMock()
    channel.guild = SimpleNamespace(id=10)
    guild = MagicMock()
    guild.name = "Мой сервер"
    guild.get_member.return_value = SimpleNamespace(mention="<@1>")
    monkeypatch.setattr(ach_mod, "resolve_channel", lambda *a, **k: channel)
    return guild, channel


async def test_event_unlocks_and_posts_card(monkeypatch):
    result = EvalResult(unlocked=[BY_ID["finds_first"]], stats=UserStats(finds_count=1))
    cog, bot, ach = make_cog(eval_result=result)
    guild, channel = _guild_with_channel(monkeypatch)
    bot.get_guild.return_value = guild

    await cog._on_find_claimed(FindClaimed(aggregate_id="10", guild_id=10, user_id=1))

    ach.evaluate.execute.assert_awaited_once_with(1, 10)
    channel.send.assert_awaited_once()
    assert channel.send.await_args.kwargs["file"].filename == "ach_finds_first.png"


async def test_event_no_unlock_posts_nothing(monkeypatch):
    cog, bot, ach = make_cog(eval_result=EvalResult([], UserStats()))
    guild, channel = _guild_with_channel(monkeypatch)
    bot.get_guild.return_value = guild

    await cog._on_find_claimed(FindClaimed(aggregate_id="10", guild_id=10, user_id=1))

    channel.send.assert_not_awaited()


async def test_award_skipped_when_module_off():
    gs = SimpleNamespace(get=lambda gid, key, default: False)  # achievements_enabled=False
    cog, bot, ach = make_cog(gs=gs)
    await cog._on_find_claimed(FindClaimed(aggregate_id="10", guild_id=10, user_id=1))
    ach.evaluate.execute.assert_not_awaited()


async def test_award_skipped_on_free_tier(monkeypatch):
    # Ачивки — Premium: событийные уведомления не идут на free-сервер (гейт tier)
    from src.application.interfaces.entitlements import PlanTier

    free = SimpleNamespace(tier=lambda gid: PlanTier.FREE)
    result = EvalResult(unlocked=[BY_ID["finds_first"]], stats=UserStats(finds_count=1))
    cog, bot, ach = make_cog(eval_result=result, entitlements=free)
    guild, channel = _guild_with_channel(monkeypatch)
    bot.get_guild.return_value = guild

    await cog._on_find_claimed(FindClaimed(aggregate_id="10", guild_id=10, user_id=1))

    ach.evaluate.execute.assert_not_awaited()  # тариф отсекает ДО пересчёта
    channel.send.assert_not_awaited()


async def test_award_ignores_empty_ids():
    cog, bot, ach = make_cog()
    await cog._on_find_claimed(FindClaimed(aggregate_id="0", guild_id=0, user_id=0))
    ach.evaluate.execute.assert_not_awaited()


async def test_achievements_command_shows_board():
    cog, bot, ach = make_cog(unlocked_ids={"finds_first"})
    interaction = make_interaction()
    await type(cog).achievements_cmd.callback(cog, interaction)

    ach.evaluate.execute.assert_awaited_once()  # ленивый добор
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert "Первая находка" in embed.description
    assert "1/" in embed.title  # одно открыто из всех
