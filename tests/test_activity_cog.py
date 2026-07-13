"""ActivityCog: каналы/отправка, приветствия/прощания, активность главного
канала и возвращение, «Альбом», календарный и войс-тик."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.application.activity.use_cases import ActivityTouch
from src.application.ai_chat.mood import MoodTracker
from src.application.relationship.use_cases import BirthdayEvents, DecayResult, RankInfo, SurveyData
from src.infrastructure.discord.cogs.activity import ActivityCog
from tests.cog_fakes import make_member

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
