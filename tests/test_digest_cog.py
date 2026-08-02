"""DigestCog: резолв имён, выбор AI/шаблон, гейт расписания (вс вечер, дедуп,
выключено, без канала). Discord/провайдер — фейки, без живого бота."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.domain.digest.entities import DigestBirthday, DigestPerson, WeeklyDigest
from src.infrastructure.discord.cogs.digest import DigestCog

GID = 10
SUNDAY_EVENING = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)  # вс 18:00 UTC ≥ 17


def make_digest(**over) -> WeeklyDigest:
    base = dict(
        week_start=date(2026, 7, 27),
        week_end=date(2026, 8, 2),
        messages=110,
        messages_prev=35,
        voice_hours=1.0,
        voice_hours_prev=2.0,
        members_now=105,
        members_delta=3,
        peak_day=date(2026, 8, 1),
        peak_day_messages=50,
        stars=(DigestPerson(1, 1200), DigestPerson(2, 800)),
        birthdays=(DigestBirthday(5, 2),),
        top_collector=DigestPerson(9, 42),
        watched_titles=("Матрица",),
    )
    base.update(over)
    return WeeklyDigest(**base)


def make_guild(names: dict[int, str]) -> MagicMock:
    guild = MagicMock()
    guild.id = GID
    guild.get_member = lambda uid: (
        SimpleNamespace(display_name=names[uid]) if uid in names else None
    )
    return guild


def make_cog(*, chat=None, settings=None, digest=None):
    bot = MagicMock()
    channel = MagicMock()
    channel.send = AsyncMock()
    bot.get_channel = MagicMock(return_value=channel)
    build = SimpleNamespace(execute=AsyncMock(return_value=digest or make_digest()))
    chat = chat or SimpleNamespace(weekly_digest=AsyncMock(return_value="AI текст"))
    settings = settings or SimpleNamespace(digest_enabled=True, digest_channel=123)
    cog = DigestCog(bot, build, chat, settings, guild_settings=None)
    return cog, bot, channel


ALL_NAMES = {1: "Аня", 2: "Боря", 5: "Вера", 9: "Гена"}


async def test_compose_uses_ai_text():
    cog, _bot, _ch = make_cog()
    text = await cog._compose(make_guild(ALL_NAMES), SUNDAY_EVENING)
    assert text == "AI текст"


async def test_compose_falls_back_to_template_on_ai_error():
    chat = SimpleNamespace(weekly_digest=AsyncMock(side_effect=RuntimeError("groq down")))
    cog, _bot, _ch = make_cog(chat=chat)
    text = await cog._compose(make_guild(ALL_NAMES), SUNDAY_EVENING)
    assert text.startswith("🌙")  # шаблонный фолбэк
    assert "Матрица" in text


async def test_compose_none_when_nothing_to_say():
    empty = make_digest(
        messages=0,
        messages_prev=0,
        voice_hours=0.0,
        members_delta=0,
        stars=(),
        birthdays=(),
        top_collector=None,
        watched_titles=(),
    )
    cog, _bot, _ch = make_cog(digest=empty)
    assert await cog._compose(make_guild({}), SUNDAY_EVENING) is None


def test_to_view_drops_members_who_left():
    cog, _bot, _ch = make_cog()
    view = cog._to_view(make_guild({1: "Аня"}), make_digest())
    assert [s.name for s in view.stars] == ["Аня"]  # user 2 ушёл — отсеян
    assert view.birthdays == ()  # user 5 не найден
    assert view.top_collector is None  # user 9 не найден
    assert view.peak_day_name == "суббота"  # 1 авг 2026


async def test_tick_posts_once_on_sunday_evening():
    cog, bot, _ch = make_cog()
    bot.guilds = [make_guild(ALL_NAMES)]
    cog._post = AsyncMock()
    await cog._tick(SUNDAY_EVENING)
    await cog._tick(SUNDAY_EVENING)  # тот же вечер — дедуп
    cog._post.assert_awaited_once()


async def test_tick_skips_off_schedule():
    cog, bot, _ch = make_cog()
    bot.guilds = [make_guild(ALL_NAMES)]
    cog._post = AsyncMock()
    await cog._tick(datetime(2026, 8, 3, 18, tzinfo=UTC))  # понедельник
    await cog._tick(datetime(2026, 8, 2, 10, tzinfo=UTC))  # вс, но утро
    cog._post.assert_not_awaited()


async def test_tick_skips_when_module_disabled():
    settings = SimpleNamespace(digest_enabled=False, digest_channel=123)
    cog, bot, _ch = make_cog(settings=settings)
    bot.guilds = [make_guild(ALL_NAMES)]
    cog._post = AsyncMock()
    await cog._tick(SUNDAY_EVENING)
    cog._post.assert_not_awaited()


async def test_tick_skips_without_channel():
    settings = SimpleNamespace(digest_enabled=True, digest_channel=0)
    cog, bot, _ch = make_cog(settings=settings)
    bot.guilds = [make_guild(ALL_NAMES)]
    cog._post = AsyncMock()
    await cog._tick(SUNDAY_EVENING)
    cog._post.assert_not_awaited()


async def test_post_sends_without_pings():
    cog, bot, channel = make_cog()
    await cog._post(make_guild(ALL_NAMES), 123, SUNDAY_EVENING)
    channel.send.assert_awaited_once()
    kwargs = channel.send.await_args.kwargs
    assert kwargs["allowed_mentions"].everyone is False
