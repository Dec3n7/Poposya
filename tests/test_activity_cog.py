"""ActivityCog: каналы/отправка, приветствия/прощания, активность главного
канала и возвращение, «Альбом», календарный и войс-тик, фоновые циклы
(дрейф настроения, снапшоты, доливка сообщений, войс-очки, скука, мысли)."""

import asyncio
import contextlib
import time
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.application.activity.use_cases import ActivityTouch
from src.application.ai_chat.mood import MoodTracker
from src.application.relationship.use_cases import BirthdayEvents, DecayResult, RankInfo, SurveyData
from src.infrastructure.discord.cogs import activity as activity_module
from src.infrastructure.discord.cogs.activity import ActivityCog
from tests.cog_fakes import http_error, make_member

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def make_settings(**over):
    base = dict(
        holidays={"11-07": "Праздник"},
        welcome_channel="bots",
        main_channel="основной",
        auto_role="",
        album_channel="альбом",
        album_reaction_emoji="",
        album_reaction_threshold=5,
        holiday_points_multiplier=2,
        birthday_remind_days=3,
        voice_points_per_hour=3,
        lonely_hours=12,
        random_thought_min_hours=3,
        random_thought_max_hours=6,
    )
    base.update(over)
    return SimpleNamespace(**base)


def make_container():
    c = SimpleNamespace()
    c.touch_activity = SimpleNamespace(
        execute=AsyncMock(return_value=ActivityTouch(returned_after_absence=False, days_absent=0))
    )
    c.try_mark_album = SimpleNamespace(execute=AsyncMock(return_value=True))
    c.load_voice_progress = SimpleNamespace(execute=AsyncMock(return_value={}))
    c.save_voice_progress = SimpleNamespace(execute=AsyncMock())
    c.record_snapshot = SimpleNamespace(execute=AsyncMock())
    c.record_message_activity = SimpleNamespace(execute=AsyncMock())
    return c


def make_relationship():
    r = SimpleNamespace()
    r.decay_points = SimpleNamespace(
        execute=AsyncMock(return_value=DecayResult(decayed=0, transfers=[]))
    )
    r.birthday_tick = SimpleNamespace(
        execute=AsyncMock(return_value=BirthdayEvents(remind=[], congratulate=[]))
    )
    r.award_point = SimpleNamespace(execute=AsyncMock())
    return r


def make_cog(container=None, relationship=None, chat=None, settings=None):
    bot = MagicMock()
    return ActivityCog(
        bot,
        container or make_container(),
        relationship or make_relationship(),
        chat,
        settings or make_settings(),
        MoodTracker(),
    )


def guild_with_channel(name, channel):
    guild = MagicMock()
    guild.text_channels = [SimpleNamespace(name="прочее"), channel]
    channel.name = name
    return guild


# --- каналы / _send ---------------------------------------------------------


def test_welcome_and_main_channel_lookup():
    cog = make_cog()
    ch_welcome = SimpleNamespace(name="bots")
    ch_main = SimpleNamespace(name="основной")
    guild = MagicMock()
    guild.text_channels = [ch_welcome, ch_main]
    assert cog._welcome_channel(guild) is ch_welcome
    assert cog._main_channel(guild) is ch_main


async def test_send_noop_for_none_channel():
    cog = make_cog()
    await cog._send(None, "text")  # не падает


async def test_send_delivers():
    cog = make_cog()
    channel = MagicMock()
    channel.send = AsyncMock()
    await cog._send(channel, "привет")
    channel.send.assert_awaited_once()


# --- join / remove ----------------------------------------------------------


async def test_member_join_fallback_welcome():
    cog = make_cog()  # chat=None
    channel = MagicMock()
    channel.send = AsyncMock()
    member = make_member(uid=1, name="Новичок")
    member.bot = False
    member.guild = MagicMock()
    member.guild.text_channels = [SimpleNamespace(name="bots")]
    # welcome-канал по имени
    cog.settings = make_settings(welcome_channel="bots")
    member.guild.text_channels[0].send = AsyncMock()
    await cog.on_member_join(member)
    member.guild.text_channels[0].send.assert_awaited_once()


async def test_member_join_ignores_bots():
    cog = make_cog()
    member = make_member(bot=True)
    await cog.on_member_join(member)  # ничего не делает, не падает


async def test_member_join_auto_role():
    cog = make_cog(settings=make_settings(auto_role="Новичок", welcome_channel="bots"))
    role = SimpleNamespace(name="Новичок")
    member = make_member(uid=1, name="X")
    member.bot = False
    member.add_roles = AsyncMock()
    member.guild = MagicMock()
    member.guild.roles = [role]
    wc = SimpleNamespace(name="bots")
    wc.send = AsyncMock()
    member.guild.text_channels = [wc]
    await cog.on_member_join(member)
    member.add_roles.assert_awaited_once()


async def test_member_remove_fallback():
    cog = make_cog()
    member = make_member(uid=1, name="Ушедший")
    member.bot = False
    wc = SimpleNamespace(name="bots")
    wc.send = AsyncMock()
    member.guild = MagicMock()
    member.guild.text_channels = [wc]
    await cog.on_member_remove(member)
    wc.send.assert_awaited_once()


# --- on_message -------------------------------------------------------------


async def test_on_message_main_channel_bumps_mood():
    cog = make_cog()
    msg = MagicMock()
    msg.author = SimpleNamespace(id=1, bot=False, display_name="Гость")
    msg.guild = SimpleNamespace(id=10)
    msg.channel = SimpleNamespace(name="основной")
    await cog.on_message(msg)
    assert cog.mood.get(10) == 52  # +2 активность в главном канале
    assert 10 not in cog._lonely_notified


async def test_on_message_returning_member_comments():
    container = make_container()
    container.touch_activity.execute.return_value = ActivityTouch(
        returned_after_absence=True, days_absent=10
    )
    chat = MagicMock()
    chat.get_rank = AsyncMock(
        return_value=RankInfo(
            points=10,
            level=2,
            role_index=0,
            is_exclusive=False,
            frozen=False,
            next_threshold=100,
            survey=SurveyData(contact="normal"),
        )
    )
    chat.freeform_remark = AsyncMock(return_value="С возвращением.")
    cog = make_cog(container, chat=chat)
    msg = MagicMock()
    msg.author = SimpleNamespace(id=1, bot=False, display_name="Гость")
    msg.guild = SimpleNamespace(id=10)
    msg.channel = MagicMock()
    msg.channel.name = "прочее"
    msg.channel.send = AsyncMock()
    await cog.on_message(msg)
    chat.freeform_remark.assert_awaited_once()
    msg.channel.send.assert_awaited_once()


async def test_on_message_returning_quiet_survey_silent():
    container = make_container()
    container.touch_activity.execute.return_value = ActivityTouch(
        returned_after_absence=True, days_absent=10
    )
    chat = MagicMock()
    chat.get_rank = AsyncMock(
        return_value=RankInfo(
            points=10,
            level=2,
            role_index=0,
            is_exclusive=False,
            frozen=False,
            next_threshold=100,
            survey=SurveyData(contact="quiet"),
        )
    )
    chat.freeform_remark = AsyncMock()
    cog = make_cog(container, chat=chat)
    msg = MagicMock()
    msg.author = SimpleNamespace(id=1, bot=False, display_name="Гость")
    msg.guild = SimpleNamespace(id=10)
    msg.channel = MagicMock()
    msg.channel.name = "прочее"
    await cog.on_message(msg)
    chat.freeform_remark.assert_not_awaited()  # «не беспокоить»


# --- альбом (on_raw_reaction_add) ------------------------------------------


def make_reaction_payload(guild_id=10, channel_id=100, message_id=200, emoji="🔥"):
    return SimpleNamespace(
        guild_id=guild_id, channel_id=channel_id, message_id=message_id, emoji=emoji
    )


async def test_album_below_threshold_skipped():
    cog = make_cog(settings=make_settings(album_reaction_threshold=5))
    album = SimpleNamespace(name="альбом", id=999)
    source = MagicMock()
    guild = MagicMock()
    guild.text_channels = [album]
    guild.get_channel.return_value = source
    cog.bot.get_guild.return_value = guild

    message = MagicMock()
    message.author = SimpleNamespace(bot=False)
    message.reactions = [SimpleNamespace(emoji="🔥", count=2)]  # < 5
    source.fetch_message = AsyncMock(return_value=message)
    await cog.on_raw_reaction_add(make_reaction_payload())
    cog.container.try_mark_album.execute.assert_not_awaited()


async def test_album_posts_when_threshold_reached():
    cog = make_cog(settings=make_settings(album_reaction_threshold=3))
    album = MagicMock()
    album.name = "альбом"
    album.id = 999
    album.send = AsyncMock()
    source = MagicMock()
    source.name = "чат"
    guild = MagicMock()
    guild.text_channels = [album]
    guild.get_channel.return_value = source
    cog.bot.get_guild.return_value = guild

    message = MagicMock()
    message.author = SimpleNamespace(
        bot=False, display_name="Автор", display_avatar=SimpleNamespace(url="http://a")
    )
    message.reactions = [SimpleNamespace(emoji="🔥", count=5)]
    message.content = "мем"
    message.attachments = []
    message.created_at = NOW
    message.jump_url = "http://jump"
    message.id = 200
    source.fetch_message = AsyncMock(return_value=message)

    await cog.on_raw_reaction_add(make_reaction_payload())
    cog.container.try_mark_album.execute.assert_awaited_once()
    album.send.assert_awaited_once()


async def test_album_dedup_skips_second():
    container = make_container()
    container.try_mark_album.execute.return_value = False  # уже публиковали
    cog = make_cog(container, settings=make_settings(album_reaction_threshold=3))
    album = MagicMock()
    album.name = "альбом"
    album.id = 999
    album.send = AsyncMock()
    source = MagicMock()
    guild = MagicMock()
    guild.text_channels = [album]
    guild.get_channel.return_value = source
    cog.bot.get_guild.return_value = guild
    message = MagicMock()
    message.author = SimpleNamespace(bot=False)
    message.reactions = [SimpleNamespace(emoji="🔥", count=10)]
    message.id = 200
    source.fetch_message = AsyncMock(return_value=message)
    await cog.on_raw_reaction_add(make_reaction_payload())
    album.send.assert_not_awaited()


# --- календарный и войс-тик --------------------------------------------------


async def test_calendar_tick_announces_holiday():
    relationship = make_relationship()
    cog = make_cog(
        relationship=relationship, settings=make_settings(holidays={"11-07": "Праздник"})
    )
    main = MagicMock()
    main.name = "основной"
    main.send = AsyncMock()
    guild = MagicMock()
    guild.id = 10
    guild.text_channels = [main]
    cog.bot.guilds = [guild]
    cog.bot.get_guild.return_value = guild

    # monkeypatch календарь на «сегодня праздник»
    cog.calendar = SimpleNamespace(holiday_name=lambda d: "Праздник")
    await cog._calendar_tick()
    main.send.assert_awaited()  # объявлен праздник
    assert cog.mood.get(10) >= 60  # +15 к настроению


async def test_calendar_tick_birthday_congratulate():
    relationship = make_relationship()
    relationship.birthday_tick.execute.return_value = BirthdayEvents(
        remind=[], congratulate=[(10, 1)]
    )
    cog = make_cog(relationship=relationship, settings=make_settings(holidays={}))
    cog.calendar = SimpleNamespace(holiday_name=lambda d: None)
    main = MagicMock()
    main.name = "основной"
    main.send = AsyncMock()
    guild = MagicMock()
    guild.id = 10
    guild.text_channels = [main]
    guild.get_member.return_value = SimpleNamespace(display_name="Именинник")
    cog.bot.guilds = [guild]
    cog.bot.get_guild.return_value = guild
    await cog._calendar_tick()
    main.send.assert_awaited()


async def test_voice_tick_awards_after_full_hour():
    relationship = make_relationship()
    cog = make_cog(relationship=relationship, settings=make_settings(voice_points_per_hour=3))
    member = SimpleNamespace(id=1, bot=False, voice=SimpleNamespace(deaf=False, self_deaf=False))
    channel = SimpleNamespace(id=50, members=[member])
    guild = MagicMock()
    guild.id = 10
    guild.voice_channels = [channel]
    guild.stage_channels = []
    guild.afk_channel = None
    cog.bot.guilds = [guild]
    # накопим 58 минут — тик доводит до 63 -> начисление
    cog._voice_minutes[(10, 1)] = 58.0
    await cog._voice_points_tick()
    relationship.award_point.execute.assert_awaited_once()


async def test_voice_tick_skips_deaf_members():
    relationship = make_relationship()
    cog = make_cog(relationship=relationship)
    member = SimpleNamespace(id=1, bot=False, voice=SimpleNamespace(deaf=True, self_deaf=False))
    channel = SimpleNamespace(id=50, members=[member])
    guild = MagicMock()
    guild.id = 10
    guild.voice_channels = [channel]
    guild.stage_channels = []
    guild.afk_channel = None
    cog.bot.guilds = [guild]
    cog._voice_minutes[(10, 1)] = 58.0
    await cog._voice_points_tick()
    relationship.award_point.execute.assert_not_awaited()  # в наушниках — не считаем


# ---------------------------------------------------------------------------
# Ниже — расширение покрытия: незакрытые ветки хендлеров и фоновые циклы.
# ---------------------------------------------------------------------------


def _rank(contact="normal"):
    return RankInfo(
        points=10,
        level=2,
        role_index=0,
        is_exclusive=False,
        frozen=False,
        next_threshold=100,
        survey=SurveyData(contact=contact),
    )


def _guild(gid=10, main_name="основной"):
    guild = MagicMock()
    guild.id = gid
    main = MagicMock()
    main.name = main_name
    main.send = AsyncMock()
    guild.text_channels = [main]
    return guild, main


async def _drive_loop(monkeypatch, loop_coro, iterations):
    """Гоняет фоновый цикл ограниченное число awaited-sleep'ов, затем рвёт его
    CancelledError'ом — без реального ожидания."""
    calls = 0

    async def fake_sleep(_):
        nonlocal calls
        calls += 1
        if calls > iterations:
            raise asyncio.CancelledError()

    monkeypatch.setattr(activity_module.asyncio, "sleep", fake_sleep)
    with contextlib.suppress(asyncio.CancelledError):
        await loop_coro


# --- cog_unload / _send -----------------------------------------------------


def test_cog_unload_cancels_tasks():
    cog = make_cog()
    task = MagicMock()
    cog._tasks = [task]
    cog.cog_unload()
    task.cancel.assert_called_once()


async def test_send_swallows_http_exception():
    cog = make_cog()
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=http_error())
    await cog._send(channel, "текст")  # не падает


async def test_send_noop_for_empty_text():
    cog = make_cog()
    channel = MagicMock()
    channel.send = AsyncMock()
    await cog._send(channel, "")
    channel.send.assert_not_called()


# --- on_member_join: авто-роль и AI-приветствие -----------------------------


async def test_member_join_auto_role_not_found_warns():
    cog = make_cog(settings=make_settings(auto_role="Нет такой", welcome_channel="bots"))
    member = make_member(uid=1, name="X")
    member.bot = False
    member.add_roles = AsyncMock()
    member.guild = MagicMock()
    member.guild.roles = []  # роли AUTO_ROLE нет
    wc = SimpleNamespace(name="bots")
    wc.send = AsyncMock()
    member.guild.text_channels = [wc]
    await cog.on_member_join(member)
    member.add_roles.assert_not_awaited()


async def test_member_join_auto_role_http_exception_swallowed():
    cog = make_cog(settings=make_settings(auto_role="Новичок", welcome_channel="bots"))
    role = SimpleNamespace(name="Новичок")
    member = make_member(uid=1, name="X")
    member.bot = False
    member.add_roles = AsyncMock(side_effect=http_error())
    member.guild = MagicMock()
    member.guild.roles = [role]
    wc = SimpleNamespace(name="bots")
    wc.send = AsyncMock()
    member.guild.text_channels = [wc]
    await cog.on_member_join(member)  # не падает
    member.add_roles.assert_awaited_once()


async def test_member_join_greetings_disabled_still_sets_auto_role():
    cog = make_cog(
        settings=make_settings(
            auto_role="Новичок", welcome_channel="bots", activity_greetings=False
        )
    )
    role = SimpleNamespace(name="Новичок")
    member = make_member(uid=1, name="X")
    member.bot = False
    member.add_roles = AsyncMock()
    member.guild = MagicMock()
    member.guild.roles = [role]
    wc = SimpleNamespace(name="bots")
    wc.send = AsyncMock()
    member.guild.text_channels = [wc]
    await cog.on_member_join(member)
    member.add_roles.assert_awaited_once()  # роль выдана
    wc.send.assert_not_awaited()  # приветствие выключено


async def test_member_join_ai_greeting_used():
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(return_value="Ну здравствуй.")
    cog = make_cog(chat=chat, settings=make_settings(welcome_channel="bots"))
    member = make_member(uid=1, name="Новичок")
    member.bot = False
    member.guild = MagicMock()
    wc = SimpleNamespace(name="bots")
    wc.send = AsyncMock()
    member.guild.text_channels = [wc]
    await cog.on_member_join(member)
    chat.freeform_remark.assert_awaited_once()
    assert wc.send.await_args.args[0] == "Ну здравствуй."


async def test_member_join_ai_greeting_exception_falls_back():
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(side_effect=RuntimeError("boom"))
    cog = make_cog(chat=chat, settings=make_settings(welcome_channel="bots"))
    member = make_member(uid=1, name="Новичок")
    member.bot = False
    member.guild = MagicMock()
    wc = SimpleNamespace(name="bots")
    wc.send = AsyncMock()
    member.guild.text_channels = [wc]
    await cog.on_member_join(member)
    wc.send.assert_awaited_once()  # ушёл фолбэк-текст


# --- on_member_remove -------------------------------------------------------


async def test_member_remove_ignores_bots():
    cog = make_cog()
    member = make_member(bot=True)
    await cog.on_member_remove(member)  # не падает, ничего не шлёт


async def test_member_remove_feature_disabled():
    cog = make_cog(settings=make_settings(activity_greetings=False))
    member = make_member(uid=1, name="Ушедший")
    member.bot = False
    wc = SimpleNamespace(name="bots")
    wc.send = AsyncMock()
    member.guild = MagicMock()
    member.guild.text_channels = [wc]
    await cog.on_member_remove(member)
    wc.send.assert_not_awaited()


async def test_member_remove_ai_farewell():
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(return_value="Скатертью.")
    cog = make_cog(chat=chat)
    member = make_member(uid=1, name="Ушедший")
    member.bot = False
    wc = SimpleNamespace(name="bots")
    wc.send = AsyncMock()
    member.guild = MagicMock()
    member.guild.text_channels = [wc]
    await cog.on_member_remove(member)
    assert wc.send.await_args.args[0] == "Скатертью."


async def test_member_remove_ai_farewell_exception_falls_back():
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(side_effect=RuntimeError("boom"))
    cog = make_cog(chat=chat)
    member = make_member(uid=1, name="Ушедший")
    member.bot = False
    wc = SimpleNamespace(name="bots")
    wc.send = AsyncMock()
    member.guild = MagicMock()
    member.guild.text_channels = [wc]
    await cog.on_member_remove(member)
    wc.send.assert_awaited_once()  # фолбэк


# --- on_message: throttle, ошибка touch -------------------------------------


async def test_on_message_ignores_bot_and_dm():
    cog = make_cog()
    bot_msg = MagicMock()
    bot_msg.author = SimpleNamespace(bot=True)
    bot_msg.guild = SimpleNamespace(id=10)
    await cog.on_message(bot_msg)
    dm_msg = MagicMock()
    dm_msg.author = SimpleNamespace(bot=False)
    dm_msg.guild = None
    await cog.on_message(dm_msg)
    cog.container.touch_activity.execute.assert_not_awaited()


async def test_on_message_throttled_skips_touch():
    cog = make_cog()
    msg = MagicMock()
    msg.author = SimpleNamespace(id=1, bot=False, display_name="Гость")
    msg.guild = SimpleNamespace(id=10)
    msg.channel = SimpleNamespace(name="прочее")
    cog._touch_throttle[(10, 1)] = time.monotonic()  # только что трогали
    await cog.on_message(msg)
    cog.container.touch_activity.execute.assert_not_awaited()


async def test_on_message_touch_exception_swallowed():
    container = make_container()
    container.touch_activity.execute.side_effect = RuntimeError("db")
    cog = make_cog(container)
    msg = MagicMock()
    msg.author = SimpleNamespace(id=1, bot=False, display_name="Гость")
    msg.guild = SimpleNamespace(id=10)
    msg.channel = SimpleNamespace(name="прочее")
    await cog.on_message(msg)  # не падает


async def test_on_message_returning_remark_exception_swallowed():
    container = make_container()
    container.touch_activity.execute.return_value = ActivityTouch(
        returned_after_absence=True, days_absent=10
    )
    chat = MagicMock()
    chat.get_rank = AsyncMock(return_value=_rank("normal"))
    chat.freeform_remark = AsyncMock(side_effect=RuntimeError("boom"))
    cog = make_cog(container, chat=chat)
    msg = MagicMock()
    msg.author = SimpleNamespace(id=1, bot=False, display_name="Гость")
    msg.guild = SimpleNamespace(id=10)
    msg.channel = MagicMock()
    msg.channel.name = "прочее"
    msg.channel.send = AsyncMock()
    await cog.on_message(msg)  # не падает


async def test_on_message_mood_bump_throttled():
    cog = make_cog()
    cog._mood_bump_throttle[10] = time.monotonic()  # только что поднимали
    msg = MagicMock()
    msg.author = SimpleNamespace(id=1, bot=False, display_name="Гость")
    msg.guild = SimpleNamespace(id=10)
    msg.channel = SimpleNamespace(name="основной")
    await cog.on_message(msg)
    assert cog.mood.get(10) == 50  # без +2


# --- on_raw_reaction_add: ранние выходы --------------------------------------


async def test_album_guild_id_none():
    cog = make_cog()
    await cog.on_raw_reaction_add(make_reaction_payload(guild_id=None))
    cog.container.try_mark_album.execute.assert_not_awaited()


async def test_album_guild_missing():
    cog = make_cog()
    cog.bot.get_guild.return_value = None
    await cog.on_raw_reaction_add(make_reaction_payload())
    cog.container.try_mark_album.execute.assert_not_awaited()


async def test_album_feature_disabled():
    cog = make_cog(settings=make_settings(activity_album=False))
    guild = MagicMock()
    guild.id = 10
    cog.bot.get_guild.return_value = guild
    await cog.on_raw_reaction_add(make_reaction_payload())
    cog.container.try_mark_album.execute.assert_not_awaited()


async def test_album_channel_none():
    cog = make_cog()
    guild = MagicMock()
    guild.id = 10
    guild.text_channels = []  # альбом-канала нет
    cog.bot.get_guild.return_value = guild
    await cog.on_raw_reaction_add(make_reaction_payload())
    cog.container.try_mark_album.execute.assert_not_awaited()


async def test_album_reaction_on_album_channel_itself_skipped():
    cog = make_cog()
    album = SimpleNamespace(name="альбом", id=100)  # реакция в самом альбоме
    guild = MagicMock()
    guild.id = 10
    guild.text_channels = [album]
    cog.bot.get_guild.return_value = guild
    await cog.on_raw_reaction_add(make_reaction_payload(channel_id=100))
    cog.container.try_mark_album.execute.assert_not_awaited()


async def test_album_emoji_filter_mismatch():
    cog = make_cog(settings=make_settings(album_reaction_emoji="⭐"))
    album = SimpleNamespace(name="альбом", id=100)
    guild = MagicMock()
    guild.id = 10
    guild.text_channels = [album]
    cog.bot.get_guild.return_value = guild
    await cog.on_raw_reaction_add(make_reaction_payload(channel_id=200, emoji="🔥"))  # не ⭐
    cog.container.try_mark_album.execute.assert_not_awaited()


async def test_album_source_channel_missing():
    cog = make_cog()
    album = SimpleNamespace(name="альбом", id=100)
    guild = MagicMock()
    guild.id = 10
    guild.text_channels = [album]
    guild.get_channel.return_value = None  # исходный канал пропал
    cog.bot.get_guild.return_value = guild
    await cog.on_raw_reaction_add(make_reaction_payload(channel_id=200))
    cog.container.try_mark_album.execute.assert_not_awaited()


async def test_album_fetch_message_http_exception():
    cog = make_cog()
    album = SimpleNamespace(name="альбом", id=100)
    source = MagicMock()
    source.fetch_message = AsyncMock(side_effect=http_error())
    guild = MagicMock()
    guild.id = 10
    guild.text_channels = [album]
    guild.get_channel.return_value = source
    cog.bot.get_guild.return_value = guild
    await cog.on_raw_reaction_add(make_reaction_payload(channel_id=200))
    cog.container.try_mark_album.execute.assert_not_awaited()


async def test_album_ignores_bot_author():
    cog = make_cog()
    album = SimpleNamespace(name="альбом", id=100)
    source = MagicMock()
    message = MagicMock()
    message.author = SimpleNamespace(bot=True)
    source.fetch_message = AsyncMock(return_value=message)
    guild = MagicMock()
    guild.id = 10
    guild.text_channels = [album]
    guild.get_channel.return_value = source
    cog.bot.get_guild.return_value = guild
    await cog.on_raw_reaction_add(make_reaction_payload(channel_id=200))
    cog.container.try_mark_album.execute.assert_not_awaited()


async def test_album_specific_emoji_counts_only_that_emoji():
    cog = make_cog(settings=make_settings(album_reaction_emoji="⭐", album_reaction_threshold=3))
    album = SimpleNamespace(name="альбом", id=100)
    source = MagicMock()
    source.name = "чат"
    message = MagicMock()
    message.author = SimpleNamespace(bot=False)
    # ⭐ ниже порога, 🔥 выше — но считать надо только ⭐
    message.reactions = [
        SimpleNamespace(emoji="⭐", count=1),
        SimpleNamespace(emoji="🔥", count=99),
    ]
    source.fetch_message = AsyncMock(return_value=message)
    guild = MagicMock()
    guild.id = 10
    guild.text_channels = [album]
    guild.get_channel.return_value = source
    cog.bot.get_guild.return_value = guild
    await cog.on_raw_reaction_add(make_reaction_payload(channel_id=200, emoji="⭐"))
    cog.container.try_mark_album.execute.assert_not_awaited()  # по ⭐ порог не набран


async def test_album_posts_with_ai_caption_and_image():
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(return_value="Экспонат дня.")
    cog = make_cog(chat=chat, settings=make_settings(album_reaction_threshold=3))
    album = MagicMock()
    album.name = "альбом"
    album.id = 100
    album.send = AsyncMock()
    source = MagicMock()
    source.name = "чат"
    message = MagicMock()
    message.author = SimpleNamespace(
        bot=False, display_name="Автор", display_avatar=SimpleNamespace(url="http://a")
    )
    message.reactions = [SimpleNamespace(emoji="🔥", count=5)]
    message.content = "текст мема"
    message.attachments = [SimpleNamespace(content_type="image/png", url="http://img")]
    message.created_at = NOW
    message.jump_url = "http://jump"
    message.id = 200
    source.fetch_message = AsyncMock(return_value=message)
    guild = MagicMock()
    guild.id = 10
    guild.text_channels = [album]
    guild.get_channel.return_value = source
    cog.bot.get_guild.return_value = guild
    await cog.on_raw_reaction_add(make_reaction_payload(channel_id=200))
    chat.freeform_remark.assert_awaited_once()
    album.send.assert_awaited_once()
    assert album.send.await_args.kwargs["content"] == "Экспонат дня."


async def test_album_caption_exception_uses_fallback():
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(side_effect=RuntimeError("boom"))
    cog = make_cog(chat=chat, settings=make_settings(album_reaction_threshold=3))
    album = MagicMock()
    album.name = "альбом"
    album.id = 100
    album.send = AsyncMock()
    source = MagicMock()
    source.name = "чат"
    message = MagicMock()
    message.author = SimpleNamespace(
        bot=False, display_name="Автор", display_avatar=SimpleNamespace(url="http://a")
    )
    message.reactions = [SimpleNamespace(emoji="🔥", count=5)]
    message.content = ""
    message.attachments = []
    message.created_at = NOW
    message.jump_url = "http://jump"
    message.id = 200
    source.fetch_message = AsyncMock(return_value=message)
    guild = MagicMock()
    guild.id = 10
    guild.text_channels = [album]
    guild.get_channel.return_value = source
    cog.bot.get_guild.return_value = guild
    await cog.on_raw_reaction_add(make_reaction_payload(channel_id=200))
    album.send.assert_awaited_once()  # фолбэк-подпись из списка


async def test_album_send_http_exception_swallowed():
    cog = make_cog(settings=make_settings(album_reaction_threshold=3))
    album = MagicMock()
    album.name = "альбом"
    album.id = 100
    album.send = AsyncMock(side_effect=http_error())
    source = MagicMock()
    source.name = "чат"
    message = MagicMock()
    message.author = SimpleNamespace(
        bot=False, display_name="Автор", display_avatar=SimpleNamespace(url="http://a")
    )
    message.reactions = [SimpleNamespace(emoji="🔥", count=5)]
    message.content = "мем"
    message.attachments = []
    message.created_at = NOW
    message.jump_url = "http://jump"
    message.id = 200
    source.fetch_message = AsyncMock(return_value=message)
    guild = MagicMock()
    guild.id = 10
    guild.text_channels = [album]
    guild.get_channel.return_value = source
    cog.bot.get_guild.return_value = guild
    await cog.on_raw_reaction_add(make_reaction_payload(channel_id=200))  # не падает


# --- on_ready ---------------------------------------------------------------


def _stub_loops(cog):
    for name in (
        "_mood_drift_loop",
        "_calendar_loop",
        "_snapshot_loop",
        "_activity_flush_loop",
        "_voice_points_loop",
        "_lonely_loop",
        "_random_thought_loop",
    ):
        setattr(cog, name, AsyncMock())


async def test_on_ready_starts_loops_with_chat():
    chat = MagicMock()
    cog = make_cog(chat=chat)
    _stub_loops(cog)
    guild = SimpleNamespace(id=10)
    cog.bot.guilds = [guild]
    try:
        await cog.on_ready()
        assert cog._loops_started is True
        assert 10 in cog._main_last_activity
        cog.container.load_voice_progress.execute.assert_awaited_once()
        # с chat запускаются все 7 циклов
        assert len(cog._tasks) == 7
    finally:
        for t in cog._tasks:
            t.cancel()
        await asyncio.sleep(0)


async def test_on_ready_without_chat_skips_lonely_and_thoughts():
    cog = make_cog()  # chat=None
    _stub_loops(cog)
    cog.bot.guilds = [SimpleNamespace(id=10)]
    try:
        await cog.on_ready()
        assert len(cog._tasks) == 5  # без «скуки» и «мыслей»
    finally:
        for t in cog._tasks:
            t.cancel()
        await asyncio.sleep(0)


async def test_on_ready_idempotent():
    cog = make_cog()
    _stub_loops(cog)
    cog.bot.guilds = [SimpleNamespace(id=10)]
    try:
        await cog.on_ready()
        first = list(cog._tasks)
        await cog.on_ready()  # второй раз — циклы не пересоздаём
        assert cog._tasks == first
    finally:
        for t in cog._tasks:
            t.cancel()
        await asyncio.sleep(0)


async def test_on_ready_voice_progress_load_exception_swallowed():
    container = make_container()
    container.load_voice_progress.execute.side_effect = RuntimeError("db")
    cog = make_cog(container)
    _stub_loops(cog)
    cog.bot.guilds = [SimpleNamespace(id=10)]
    try:
        await cog.on_ready()  # не падает
        assert cog._loops_started is True
    finally:
        for t in cog._tasks:
            t.cancel()
        await asyncio.sleep(0)


# --- фоновые циклы: обёртки (while/try/except/sleep) --------------------------


async def test_calendar_loop_runs_tick_and_survives_error(monkeypatch):
    cog = make_cog()
    cog._calendar_tick = AsyncMock(side_effect=RuntimeError("boom"))
    await _drive_loop(monkeypatch, cog._calendar_loop(), iterations=0)
    cog._calendar_tick.assert_awaited_once()


async def test_snapshot_loop_runs_tick_and_survives_error(monkeypatch):
    cog = make_cog()
    cog._snapshot_tick = AsyncMock(side_effect=RuntimeError("boom"))
    await _drive_loop(monkeypatch, cog._snapshot_loop(), iterations=0)
    cog._snapshot_tick.assert_awaited_once()


async def test_activity_flush_loop_runs_and_survives_error(monkeypatch):
    cog = make_cog()
    cog._flush_message_counts = AsyncMock(side_effect=RuntimeError("boom"))
    await _drive_loop(monkeypatch, cog._activity_flush_loop(), iterations=1)
    cog._flush_message_counts.assert_awaited_once()


async def test_voice_points_loop_runs_and_survives_error(monkeypatch):
    cog = make_cog()
    cog._voice_points_tick = AsyncMock(side_effect=RuntimeError("boom"))
    await _drive_loop(monkeypatch, cog._voice_points_loop(), iterations=1)
    cog._voice_points_tick.assert_awaited_once()


async def test_mood_drift_loop_drifts_active_and_idle(monkeypatch):
    cog = make_cog()
    active_guild = SimpleNamespace(id=1)
    idle_guild = SimpleNamespace(id=2)
    stale_guild = SimpleNamespace(id=3)  # никогда не было активности
    cog.bot.guilds = [active_guild, idle_guild, stale_guild]
    now = time.monotonic()
    cog._main_last_activity[1] = now  # свежая
    cog._main_last_activity[2] = now - 3 * 3600  # тишина > 2 ч
    await _drive_loop(monkeypatch, cog._mood_drift_loop(), iterations=1)
    assert cog.mood.get(1) > 50  # дрейф к активной цели
    assert cog.mood.get(2) < 50  # дрейф к тихой цели


# --- _calendar_tick: праздники, угасание, дни рождения -----------------------


async def test_calendar_tick_holiday_dedup_and_feature_off():
    relationship = make_relationship()
    cog = make_cog(relationship=relationship)
    cog.calendar = SimpleNamespace(holiday_name=lambda d: "Праздник")
    guild_on, main_on = _guild(10)
    guild_off = MagicMock()
    guild_off.id = 20
    cog.settings = make_settings()
    # у сервера 20 праздники выключены
    cog.gs = SimpleNamespace(
        get=lambda gid, key, default: (
            False if (gid == 20 and key == "activity_holidays") else default
        )
    )
    cog.bot.guilds = [guild_on, guild_off]
    cog.bot.get_guild.side_effect = lambda gid: {10: guild_on, 20: guild_off}.get(gid)
    await cog._calendar_tick()
    main_on.send.assert_awaited()  # сервер 10 получил объявление
    # повторный тик в тот же день — дедуп, второго объявления нет
    main_on.send.reset_mock()
    await cog._calendar_tick()
    main_on.send.assert_not_awaited()


async def test_calendar_tick_holiday_with_chat_and_exception():
    relationship = make_relationship()
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(side_effect=RuntimeError("boom"))
    cog = make_cog(relationship=relationship, chat=chat)
    cog.calendar = SimpleNamespace(holiday_name=lambda d: "Праздник")
    guild, main = _guild(10)
    cog.bot.guilds = [guild]
    cog.bot.get_guild.return_value = guild
    await cog._calendar_tick()
    main.send.assert_awaited()  # фолбэк-текст после сбоя генерации


async def test_calendar_tick_logs_decay():
    relationship = make_relationship()
    relationship.decay_points.execute.return_value = DecayResult(decayed=3, transfers=[(1, 2)])
    cog = make_cog(relationship=relationship, settings=make_settings(holidays={}))
    cog.calendar = SimpleNamespace(holiday_name=lambda d: None)
    cog.bot.guilds = []
    await cog._calendar_tick()  # покрывает ветку логирования угасания
    relationship.decay_points.execute.assert_awaited_once()


async def test_calendar_tick_birthday_remind():
    relationship = make_relationship()
    relationship.birthday_tick.execute.return_value = BirthdayEvents(
        remind=[(10, 1)], congratulate=[]
    )
    cog = make_cog(relationship=relationship, settings=make_settings(holidays={}))
    cog.calendar = SimpleNamespace(holiday_name=lambda d: None)
    guild, main = _guild(10)
    cog.bot.guilds = [guild]
    cog.bot.get_guild.return_value = guild
    await cog._calendar_tick()
    main.send.assert_awaited()  # напоминание о ДР


async def test_calendar_tick_birthday_remind_feature_off_and_no_channel():
    relationship = make_relationship()
    relationship.birthday_tick.execute.return_value = BirthdayEvents(
        remind=[(10, 1), (20, 2)], congratulate=[]
    )
    cog = make_cog(relationship=relationship, settings=make_settings(holidays={}))
    cog.calendar = SimpleNamespace(holiday_name=lambda d: None)
    # сервер 10: ДР выключены; сервер 20: канала «основной» нет
    cog.gs = SimpleNamespace(
        get=lambda gid, key, default: (
            False if (gid == 10 and key == "activity_birthdays") else default
        )
    )
    guild10 = MagicMock()
    guild10.id = 10
    guild20 = MagicMock()
    guild20.id = 20
    guild20.text_channels = []  # нет главного канала
    cog.bot.guilds = []
    cog.bot.get_guild.side_effect = lambda gid: {10: guild10, 20: guild20}.get(gid)
    await cog._calendar_tick()  # обе ветки-continue отработали, без падений


async def test_calendar_tick_birthday_congratulate_feature_off_and_no_channel():
    relationship = make_relationship()
    relationship.birthday_tick.execute.return_value = BirthdayEvents(
        remind=[], congratulate=[(10, 1), (20, 2)]
    )
    cog = make_cog(relationship=relationship, settings=make_settings(holidays={}))
    cog.calendar = SimpleNamespace(holiday_name=lambda d: None)
    cog.gs = SimpleNamespace(
        get=lambda gid, key, default: (
            False if (gid == 10 and key == "activity_birthdays") else default
        )
    )
    guild10 = MagicMock()
    guild10.id = 10
    guild20 = MagicMock()
    guild20.id = 20
    guild20.text_channels = []  # нет главного канала
    cog.bot.guilds = []
    cog.bot.get_guild.side_effect = lambda gid: {10: guild10, 20: guild20}.get(gid)
    await cog._calendar_tick()  # обе ветки-continue отработали


async def test_calendar_tick_birthday_remind_guild_missing():
    relationship = make_relationship()
    relationship.birthday_tick.execute.return_value = BirthdayEvents(
        remind=[(10, 1)], congratulate=[]
    )
    cog = make_cog(relationship=relationship, settings=make_settings(holidays={}))
    cog.calendar = SimpleNamespace(holiday_name=lambda d: None)
    cog.bot.guilds = []
    cog.bot.get_guild.return_value = None  # гильдия пропала
    await cog._calendar_tick()  # не падает


async def test_calendar_tick_birthday_remind_send_http_exception():
    relationship = make_relationship()
    relationship.birthday_tick.execute.return_value = BirthdayEvents(
        remind=[(10, 1)], congratulate=[]
    )
    cog = make_cog(relationship=relationship, settings=make_settings(holidays={}))
    cog.calendar = SimpleNamespace(holiday_name=lambda d: None)
    guild, main = _guild(10)
    main.send.side_effect = http_error()
    cog.bot.guilds = [guild]
    cog.bot.get_guild.return_value = guild
    await cog._calendar_tick()  # не падает


async def test_calendar_tick_birthday_congratulate_with_chat():
    relationship = make_relationship()
    relationship.birthday_tick.execute.return_value = BirthdayEvents(
        remind=[], congratulate=[(10, 1)]
    )
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(return_value="Расти большой.")
    cog = make_cog(relationship=relationship, chat=chat, settings=make_settings(holidays={}))
    cog.calendar = SimpleNamespace(holiday_name=lambda d: None)
    guild, main = _guild(10)
    guild.get_member.return_value = SimpleNamespace(display_name="Именинник")
    cog.bot.guilds = [guild]
    cog.bot.get_guild.return_value = guild
    await cog._calendar_tick()
    main.send.assert_awaited()
    assert "Расти большой." in main.send.await_args.args[0]


async def test_calendar_tick_birthday_congratulate_chat_exception():
    relationship = make_relationship()
    relationship.birthday_tick.execute.return_value = BirthdayEvents(
        remind=[], congratulate=[(10, 1)]
    )
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(side_effect=RuntimeError("boom"))
    cog = make_cog(relationship=relationship, chat=chat, settings=make_settings(holidays={}))
    cog.calendar = SimpleNamespace(holiday_name=lambda d: None)
    guild, main = _guild(10)
    guild.get_member.return_value = None  # ник неизвестен → «именинник»
    cog.bot.guilds = [guild]
    cog.bot.get_guild.return_value = guild
    await cog._calendar_tick()
    main.send.assert_awaited()  # ушёл фолбэк-текст


async def test_calendar_tick_birthday_congratulate_guild_missing():
    relationship = make_relationship()
    relationship.birthday_tick.execute.return_value = BirthdayEvents(
        remind=[], congratulate=[(10, 1)]
    )
    cog = make_cog(relationship=relationship, settings=make_settings(holidays={}))
    cog.calendar = SimpleNamespace(holiday_name=lambda d: None)
    cog.bot.guilds = []
    cog.bot.get_guild.return_value = None
    await cog._calendar_tick()  # не падает


async def test_calendar_tick_birthday_congratulate_send_http_exception():
    relationship = make_relationship()
    relationship.birthday_tick.execute.return_value = BirthdayEvents(
        remind=[], congratulate=[(10, 1)]
    )
    cog = make_cog(relationship=relationship, settings=make_settings(holidays={}))
    cog.calendar = SimpleNamespace(holiday_name=lambda d: None)
    guild, main = _guild(10)
    guild.get_member.return_value = SimpleNamespace(display_name="Именинник")
    main.send.side_effect = http_error()
    cog.bot.guilds = [guild]
    cog.bot.get_guild.return_value = guild
    await cog._calendar_tick()  # не падает


# --- _snapshot_tick ---------------------------------------------------------


async def test_snapshot_tick_records_once_per_day():
    container = make_container()
    cog = make_cog(container)
    guild = MagicMock()
    guild.id = 10
    guild.member_count = 42
    cog.bot.guilds = [guild]
    await cog._snapshot_tick()
    container.record_snapshot.execute.assert_awaited_once()
    # повтор в тот же день — дедуп
    container.record_snapshot.execute.reset_mock()
    await cog._snapshot_tick()
    container.record_snapshot.execute.assert_not_awaited()


async def test_snapshot_tick_exception_does_not_mark_done():
    container = make_container()
    container.record_snapshot.execute.side_effect = RuntimeError("db")
    cog = make_cog(container)
    guild = MagicMock()
    guild.id = 10
    guild.member_count = 5
    cog.bot.guilds = [guild]
    await cog._snapshot_tick()  # не падает
    key = (10, date.today().isoformat())
    # снапшот не помечен снятым — на следующем тике попробуем снова
    assert key not in cog._snapshot_taken or True  # ключ не добавлен при сбое
    assert (10, date.today().isoformat()) not in cog._snapshot_taken


# --- _flush_message_counts --------------------------------------------------


async def test_flush_message_counts_empty_is_noop():
    container = make_container()
    cog = make_cog(container)
    await cog._flush_message_counts()
    container.record_message_activity.execute.assert_not_awaited()


async def test_flush_message_counts_writes_per_guild():
    container = make_container()
    cog = make_cog(container)
    today = date.today()
    cog._msg_counts = {(10, today, 12): 3, (20, today, 13): 1}
    await cog._flush_message_counts()
    assert container.record_message_activity.execute.await_count == 2
    assert cog._msg_counts == {}  # снято атомарно


async def test_flush_message_counts_returns_buckets_on_failure():
    container = make_container()
    container.record_message_activity.execute.side_effect = RuntimeError("db")
    cog = make_cog(container)
    today = date.today()
    cog._msg_counts = {(10, today, 12): 3}
    await cog._flush_message_counts()
    # несохранённое вернулось обратно в счётчик
    assert cog._msg_counts.get((10, today, 12)) == 3


# --- _voice_points_tick: доп. ветки -----------------------------------------


async def test_voice_tick_feature_disabled_skips_guild():
    relationship = make_relationship()
    cog = make_cog(relationship=relationship, settings=make_settings(activity_voice_points=False))
    guild = MagicMock()
    guild.id = 10
    cog.bot.guilds = [guild]
    await cog._voice_points_tick()
    relationship.award_point.execute.assert_not_awaited()


async def test_voice_tick_zero_per_hour_skips_guild():
    relationship = make_relationship()
    cog = make_cog(relationship=relationship, settings=make_settings(voice_points_per_hour=0))
    guild = MagicMock()
    guild.id = 10
    guild.voice_channels = []
    guild.stage_channels = []
    cog.bot.guilds = [guild]
    await cog._voice_points_tick()
    relationship.award_point.execute.assert_not_awaited()


async def test_voice_tick_skips_afk_and_bots():
    relationship = make_relationship()
    cog = make_cog(relationship=relationship, settings=make_settings(voice_points_per_hour=3))
    afk = SimpleNamespace(id=99, members=[])
    bot_member = SimpleNamespace(id=2, bot=True, voice=SimpleNamespace(deaf=False, self_deaf=False))
    channel = SimpleNamespace(id=50, members=[bot_member])
    guild = MagicMock()
    guild.id = 10
    guild.voice_channels = [afk, channel]
    guild.stage_channels = []
    guild.afk_channel = SimpleNamespace(id=99)
    cog.bot.guilds = [guild]
    await cog._voice_points_tick()
    relationship.award_point.execute.assert_not_awaited()  # afk пропущен, бот пропущен


async def test_voice_tick_accumulates_without_award_under_hour():
    relationship = make_relationship()
    cog = make_cog(relationship=relationship, settings=make_settings(voice_points_per_hour=3))
    member = SimpleNamespace(id=1, bot=False, voice=SimpleNamespace(deaf=False, self_deaf=False))
    channel = SimpleNamespace(id=50, members=[member])
    guild = MagicMock()
    guild.id = 10
    guild.voice_channels = [channel]
    guild.stage_channels = []
    guild.afk_channel = None
    cog.bot.guilds = [guild]
    await cog._voice_points_tick()
    relationship.award_point.execute.assert_not_awaited()  # ещё не набрал час
    assert cog._voice_minutes[(10, 1)] == cog._VOICE_TICK_SECONDS / 60


async def test_voice_tick_no_voice_state_skipped():
    relationship = make_relationship()
    cog = make_cog(relationship=relationship, settings=make_settings(voice_points_per_hour=3))
    member = SimpleNamespace(id=1, bot=False, voice=None)  # состояние неизвестно
    channel = SimpleNamespace(id=50, members=[member])
    guild = MagicMock()
    guild.id = 10
    guild.voice_channels = [channel]
    guild.stage_channels = []
    guild.afk_channel = None
    cog.bot.guilds = [guild]
    await cog._voice_points_tick()
    relationship.award_point.execute.assert_not_awaited()


async def test_voice_tick_save_progress_exception_swallowed():
    container = make_container()
    container.save_voice_progress.execute.side_effect = RuntimeError("db")
    relationship = make_relationship()
    cog = make_cog(container, relationship, settings=make_settings(voice_points_per_hour=3))
    member = SimpleNamespace(id=1, bot=False, voice=SimpleNamespace(deaf=False, self_deaf=False))
    channel = SimpleNamespace(id=50, members=[member])
    guild = MagicMock()
    guild.id = 10
    guild.voice_channels = [channel]
    guild.stage_channels = []
    guild.afk_channel = None
    cog.bot.guilds = [guild]
    await cog._voice_points_tick()  # не падает


# --- _lonely_loop / _random_thought_loop ------------------------------------


async def test_lonely_loop_posts_when_silent(monkeypatch):
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(return_value="Скучно тут без вас.")
    cog = make_cog(chat=chat, settings=make_settings(lonely_hours=1))
    guild, main = _guild(10)
    cog.bot.guilds = [guild]
    cog.bot.get_guild.return_value = guild
    cog._main_last_activity[10] = time.monotonic() - 2 * 3600  # тишина 2 ч > 1 ч
    await _drive_loop(monkeypatch, cog._lonely_loop(), iterations=1)
    main.send.assert_awaited()
    assert 10 in cog._lonely_notified


async def test_lonely_loop_skips_already_notified_and_feature_off(monkeypatch):
    chat = MagicMock()
    chat.freeform_remark = AsyncMock()
    cog = make_cog(chat=chat, settings=make_settings(lonely_hours=1))
    notified_guild = SimpleNamespace(id=10)  # уже уведомляли
    feature_off_guild = SimpleNamespace(id=20)  # «скука» выключена
    cog.bot.guilds = [notified_guild, feature_off_guild]
    cog._main_last_activity[10] = time.monotonic() - 2 * 3600
    cog._main_last_activity[20] = time.monotonic() - 2 * 3600
    cog._lonely_notified.add(10)
    cog.gs = SimpleNamespace(
        get=lambda gid, key, default: False if (gid == 20 and key == "activity_lonely") else default
    )
    await _drive_loop(monkeypatch, cog._lonely_loop(), iterations=1)
    chat.freeform_remark.assert_not_awaited()  # обе ветки-continue


async def test_lonely_loop_skips_when_recently_active(monkeypatch):
    chat = MagicMock()
    chat.freeform_remark = AsyncMock()
    cog = make_cog(chat=chat, settings=make_settings(lonely_hours=12))
    guild, main = _guild(10)
    cog.bot.guilds = [guild]
    cog._main_last_activity[10] = time.monotonic()  # только что писали
    await _drive_loop(monkeypatch, cog._lonely_loop(), iterations=1)
    chat.freeform_remark.assert_not_awaited()


async def test_lonely_loop_exception_swallowed(monkeypatch):
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(side_effect=RuntimeError("boom"))
    cog = make_cog(chat=chat, settings=make_settings(lonely_hours=1))
    guild, main = _guild(10)
    cog.bot.guilds = [guild]
    cog.bot.get_guild.return_value = guild
    cog._main_last_activity[10] = time.monotonic() - 2 * 3600
    await _drive_loop(monkeypatch, cog._lonely_loop(), iterations=1)  # не падает


async def test_random_thought_loop_posts_when_active(monkeypatch):
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(return_value="Дождь за окном.")
    cog = make_cog(chat=chat)
    guild, main = _guild(10)
    cog.bot.guilds = [guild]
    cog.bot.get_guild.return_value = guild
    cog._main_last_activity[10] = time.monotonic()  # активность в последний час
    await _drive_loop(monkeypatch, cog._random_thought_loop(), iterations=1)
    main.send.assert_awaited()


async def test_random_thought_loop_feature_off_skips(monkeypatch):
    chat = MagicMock()
    chat.freeform_remark = AsyncMock()
    cog = make_cog(chat=chat, settings=make_settings(activity_random_thoughts=False))
    guild, main = _guild(10)
    cog.bot.guilds = [guild]
    cog._main_last_activity[10] = time.monotonic()  # активность есть
    await _drive_loop(monkeypatch, cog._random_thought_loop(), iterations=1)
    chat.freeform_remark.assert_not_awaited()  # подфункция выключена


async def test_random_thought_loop_skips_when_stale(monkeypatch):
    chat = MagicMock()
    chat.freeform_remark = AsyncMock()
    cog = make_cog(chat=chat)
    guild, main = _guild(10)
    cog.bot.guilds = [guild]
    cog._main_last_activity[10] = time.monotonic() - 2 * 3600  # активности не было
    await _drive_loop(monkeypatch, cog._random_thought_loop(), iterations=1)
    chat.freeform_remark.assert_not_awaited()


async def test_random_thought_loop_exception_swallowed(monkeypatch):
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(side_effect=RuntimeError("boom"))
    cog = make_cog(chat=chat)
    guild, main = _guild(10)
    cog.bot.guilds = [guild]
    cog.bot.get_guild.return_value = guild
    cog._main_last_activity[10] = time.monotonic()
    await _drive_loop(monkeypatch, cog._random_thought_loop(), iterations=1)  # не падает
