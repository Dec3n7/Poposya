"""FunCog: кости (одиночные/дуэль), монетка, тема, профиль (+витрина/бар/
last_seen), день рождения, правила, статистика, /send, /remind."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.application.relationship.use_cases import RankInfo, SurveyData
from src.domain.relationship.policies import PointsToLevelPolicy
from src.infrastructure.discord.cogs.fun import FunCog
from tests.cog_fakes import forbidden, make_interaction, make_member

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def make_rank(**over):
    base = dict(
        points=300,
        level=3,
        role_index=1,
        is_exclusive=False,
        frozen=False,
        next_threshold=450,
        user_notes="",
        survey=SurveyData(),
        birthday_day=None,
        birthday_month=None,
        deep_dialogs=0,
        last_dialog_at=None,
    )
    base.update(over)
    return RankInfo(**base)


def make_settings(**over):
    base = dict(send_per_hour=5, log_channel=0)
    base.update(over)
    return SimpleNamespace(**base)


def make_relationship():
    r = SimpleNamespace()
    r.policy = PointsToLevelPolicy()
    r.role_names = ["R0", "R1", "R2", "R3", "R4", "R5", "R6"]
    r.get_rank = SimpleNamespace(execute=AsyncMock(return_value=make_rank()))
    r.set_birthday = SimpleNamespace(execute=AsyncMock(return_value=True))
    return r


def make_activity():
    a = SimpleNamespace()
    a.get_voice_hours = SimpleNamespace(execute=AsyncMock(return_value=0.0))
    a.add_reminder = SimpleNamespace(execute=AsyncMock())
    a.pop_due_reminders = SimpleNamespace(execute=AsyncMock(return_value=[]))
    return a


def make_cog(relationship=None, activity=None, settings=None, **kw):
    bot = MagicMock()
    return FunCog(
        bot,
        activity or make_activity(),
        relationship or make_relationship(),
        None,
        settings or make_settings(),
        **kw,
    )


# --- dice / coinflip / topic ------------------------------------------------


async def test_dice_single_roll(monkeypatch):
    monkeypatch.setattr("src.infrastructure.discord.cogs.fun.random.randint", lambda a, b: 4)
    cog = make_cog()
    interaction = make_interaction()
    interaction.guild = None
    await type(cog).dice.callback(cog, interaction, 6, None)
    assert "→ **4**" in interaction.response.send_message.await_args.args[0]


async def test_dice_rejects_only_bots():
    cog = make_cog()
    interaction = make_interaction()
    bot_member = make_member(uid=5, bot=True)
    interaction.guild.get_member = lambda uid: bot_member
    await type(cog).dice.callback(cog, interaction, 6, "<@5>")
    assert "Боты в кости" in interaction.response.send_message.await_args.args[0]


async def test_dice_duel_has_winner(monkeypatch):
    rolls = iter([6, 1, 6, 1])  # раунд1: p1=6,p2=1 -> p1 победил
    monkeypatch.setattr(
        "src.infrastructure.discord.cogs.fun.random.randint", lambda a, b: next(rolls)
    )
    cog = make_cog()
    interaction = make_interaction(user_id=1)
    opponent = make_member(uid=2, name="Соперник")
    interaction.guild.get_member = lambda uid: opponent if uid == 2 else None
    await type(cog).dice.callback(cog, interaction, 6, "<@2>")
    assert "Победа" in interaction.response.send_message.await_args.args[0]


async def test_dice_too_many_players():
    cog = make_cog()
    interaction = make_interaction(user_id=1)
    members = {i: make_member(uid=i, name=f"P{i}") for i in range(2, 30)}
    interaction.guild.get_member = lambda uid: members.get(uid)
    mentions = "".join(f"<@{i}>" for i in range(2, 30))
    await type(cog).dice.callback(cog, interaction, 6, mentions)
    assert "лотерея" in interaction.response.send_message.await_args.args[0]


async def test_coinflip():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).coinflip.callback(cog, interaction)
    assert interaction.response.send_message.await_args.args[0]


async def test_topic():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).topic.callback(cog, interaction)
    assert "💬" in interaction.response.send_message.await_args.args[0]


# --- helpers ----------------------------------------------------------------


def test_relationship_bar_full_when_no_next():
    cog = make_cog()
    assert cog._relationship_bar(2000, None) == "▰" * 10


def test_relationship_bar_partial():
    cog = make_cog()
    bar = cog._relationship_bar(150, 250)  # между 100 и 250
    assert "▰" in bar and "▱" in bar and len(bar) == 10


def test_last_seen_variants():
    cog = make_cog()
    assert "ни разу" in cog._last_seen(10, None)
    assert "сегодня" in cog._last_seen(10, datetime.now(UTC))
    assert "вчера" in cog._last_seen(10, datetime.now(UTC) - timedelta(days=1))
    assert "3 дн" in cog._last_seen(10, datetime.now(UTC) - timedelta(days=3))


# --- profile ----------------------------------------------------------------


async def test_profile_bot_has_no_soul():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).profile.callback(cog, interaction, make_member(bot=True))
    assert "нет души" in interaction.response.send_message.await_args.args[0]


async def test_profile_builds_embed():
    relationship = make_relationship()
    relationship.get_rank.execute.return_value = make_rank(
        level=5,
        points=800,
        role_index=4,
        deep_dialogs=6,
        survey=SurveyData(interests="Игры", season="лето", completed=True),
        birthday_day=2,
        birthday_month=6,
        is_exclusive=False,
    )
    cog = make_cog(relationship)
    interaction = make_interaction()
    target = make_member(uid=1, name="Гость")
    target.display_avatar = SimpleNamespace(url="http://a")
    await type(cog).profile.callback(cog, interaction, target)
    embed = interaction.followup.send.await_args.kwargs["embed"]
    field_names = [f.name for f in embed.fields]
    assert "Привязанность" in field_names
    assert "Что я знаю о тебе…" in field_names


async def test_profile_showcase_includes_voice_hours():
    activity = make_activity()
    activity.get_voice_hours.execute.return_value = 3.5
    cog = make_cog(activity=activity)
    target = make_member(uid=1)
    target.display_avatar = SimpleNamespace(url="http://a")
    lines = await cog._build_showcase(10, 1)
    assert any("3.5 ч" in ln for ln in lines)


# --- birthday ---------------------------------------------------------------


async def test_birthday_valid():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).birthday.callback(cog, interaction, 2, 6)
    assert "Записала" in interaction.followup.send.await_args.args[0]


async def test_birthday_invalid():
    relationship = make_relationship()
    relationship.set_birthday.execute.return_value = False
    cog = make_cog(relationship)
    interaction = make_interaction()
    await type(cog).birthday.callback(cog, interaction, 31, 2)
    assert "не существует" in interaction.followup.send.await_args.args[0]


# --- rules / serverstats ----------------------------------------------------


async def test_rules():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).rules.callback(cog, interaction)
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert "Правила" in embed.title


async def test_serverstats():
    cog = make_cog()
    interaction = make_interaction()
    guild = interaction.guild
    guild.members = [SimpleNamespace(bot=False), SimpleNamespace(bot=True)]
    guild.member_count = 2
    guild.name = "Сервер"
    guild.icon = None
    guild.text_channels = [1, 2]
    guild.voice_channels = [1]
    guild.premium_subscription_count = 3
    guild.premium_tier = 1
    guild.created_at = NOW
    guild.owner = SimpleNamespace(__str__=lambda s: "Owner")
    await type(cog).serverstats.callback(cog, interaction)
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert "Участников" in [f.name for f in embed.fields]


# --- send -------------------------------------------------------------------


async def test_send_to_bot_rejected():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).send.callback(cog, interaction, make_member(bot=True), "hi", "открыто")
    assert "Ботам письма" in interaction.response.send_message.await_args.args[0]


async def test_send_to_self_rejected():
    cog = make_cog()
    interaction = make_interaction(user_id=1)
    await type(cog).send.callback(cog, interaction, make_member(uid=1), "hi", "открыто")
    assert "самому себе" in interaction.response.send_message.await_args.args[0]


async def test_send_success():
    cog = make_cog()
    interaction = make_interaction(user_id=1)
    interaction.guild.name = "Сервер"
    recipient = make_member(uid=2)
    recipient.send = AsyncMock()
    await type(cog).send.callback(cog, interaction, recipient, "привет", "открыто")
    recipient.send.assert_awaited_once()
    assert "Доставлено" in interaction.followup.send.await_args.args[0]


async def test_send_forbidden_dm():
    cog = make_cog()
    interaction = make_interaction(user_id=1)
    interaction.guild.name = "Сервер"
    recipient = make_member(uid=2)
    recipient.send = AsyncMock(side_effect=forbidden())
    await type(cog).send.callback(cog, interaction, recipient, "привет", "анонимно")
    assert "закрыты личные" in interaction.followup.send.await_args.args[0]


async def test_send_rate_limited():
    cog = make_cog(settings=make_settings(send_per_hour=1))
    interaction = make_interaction(user_id=1)
    interaction.guild.name = "Сервер"
    recipient = make_member(uid=2)
    recipient.send = AsyncMock()
    await type(cog).send.callback(cog, interaction, recipient, "1", "открыто")
    # второй раз — лимит
    interaction2 = make_interaction(user_id=1)
    interaction2.guild.name = "Сервер"
    await type(cog).send.callback(cog, interaction2, recipient, "2", "открыто")
    assert "почтовое отделение" in interaction2.response.send_message.await_args.args[0]


# --- remind -----------------------------------------------------------------


async def test_remind_schedules():
    activity = make_activity()
    cog = make_cog(activity=activity)
    interaction = make_interaction()
    await type(cog).remind.callback(cog, interaction, 30, "позвонить")
    activity.add_reminder.execute.assert_awaited_once()
    assert "Напомню" in interaction.response.send_message.await_args.args[0]
