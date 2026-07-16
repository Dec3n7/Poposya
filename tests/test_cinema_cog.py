"""CinemaCog: хелперы (_trim/_title_of/_ts/_parse_when), /movie add/list/remove/
watched/top, голосование 👍/👎, кнопки оценок."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from src.application.cinema.use_cases import (
    AddMovieResult,
    FinalizeResult,
    MovieReview,
    RateResult,
    VoteResult,
)
from src.domain.cinema.entities import MovieEntry
from src.infrastructure.cinema.provider import MovieInfo
from src.infrastructure.discord.cogs.cinema import (
    CinemaCardView,
    CinemaCog,
    CinemaRatingView,
    _title_of,
    _trim,
    _ts,
)
from tests.cog_fakes import make_interaction

NOW = datetime(2026, 7, 11, 20, 0, tzinfo=UTC)


def make_entry(title="Фильм", **over):
    base = dict(guild_id=10, title=title, added_by=1, added_at=NOW)
    base.update(over)
    return MovieEntry(**base)


def make_settings(**over):
    base = dict(
        cinema_watchlist_max=50,
        cinema_utc_offset=3,
        cinema_poll_options=5,
        cinema_rating_hours=24,
        cinema_forum_channel=0,
    )
    base.update(over)
    return SimpleNamespace(**base)


def make_container():
    c = SimpleNamespace()
    c.add_movie = SimpleNamespace(execute=AsyncMock())
    c.register_message = SimpleNamespace(execute=AsyncMock())
    c.vote_movie = SimpleNamespace(execute=AsyncMock())
    c.list_watchlist = SimpleNamespace(execute=AsyncMock(return_value=[]))
    c.top_watched = SimpleNamespace(execute=AsyncMock(return_value=[]))
    c.remove_movie = SimpleNamespace(execute=AsyncMock(return_value=("ok", None)))
    c.open_rating = SimpleNamespace(execute=AsyncMock(return_value=None))
    c.rate_movie = SimpleNamespace(execute=AsyncMock())
    c.review_movie = SimpleNamespace(execute=AsyncMock())
    c.list_reviews = SimpleNamespace(execute=AsyncMock(return_value=[]))
    c.list_ratings = SimpleNamespace(execute=AsyncMock(return_value=[]))
    c.get_movie = SimpleNamespace(execute=AsyncMock(return_value=None))
    c.finalize_rating = SimpleNamespace(execute=AsyncMock(return_value=None))
    return c


def make_search(enabled=True, results=None):
    return SimpleNamespace(enabled=enabled, search=AsyncMock(return_value=results or []))


def make_cog(container=None, search=None, settings=None):
    bot = MagicMock()
    return CinemaCog(
        bot,
        container or make_container(),
        MagicMock(),
        None,
        settings or make_settings(),
        MagicMock(),
        search or make_search(),
    )


# --- pure helpers -----------------------------------------------------------


def test_trim():
    assert _trim("короткий", 20) == "короткий"
    assert _trim("x" * 30, 10) == "x" * 9 + "…"


def test_title_of():
    assert _title_of(make_entry("A", year=2010)) == "A (2010)"
    assert _title_of(make_entry("B", year=None)) == "B"


def test_ts():
    assert _ts(NOW) == f"<t:{int(NOW.timestamp())}:R>"
    assert _ts(NOW, "D").endswith(":D>")


def test_parse_when_tomorrow_and_auto_advance():
    cog = make_cog()
    # «завтра» — всегда в будущем, независимо от времени суток
    assert cog._parse_when("завтра", "20:30") is not None
    # без даты прошедшее время автоматически переносится на завтра -> не None
    assert cog._parse_when(None, "00:01") is not None


def test_parse_when_explicit_date():
    from datetime import timedelta
    from datetime import timezone as _tz

    cog = make_cog()
    # завтрашняя дата в формате ДД.ММ — гарантированно в будущем
    tz = _tz(timedelta(hours=3))
    tomorrow = (datetime.now(tz) + timedelta(days=1)).date()
    dt = cog._parse_when(f"{tomorrow.day:02d}.{tomorrow.month:02d}", "18:00")
    assert dt is not None and dt.day == tomorrow.day


def test_parse_when_invalid():
    cog = make_cog()
    assert cog._parse_when(None, "25:00") is None  # час вне диапазона
    assert cog._parse_when(None, "нея") is None  # не время
    assert cog._parse_when("31.02", "10:00") is None  # несуществующая дата


# --- /movie add + add_entry -------------------------------------------------


async def test_movie_add_multiple_offers_picker():
    search = make_search(results=[MovieInfo(1, "A", 2000, "", ""), MovieInfo(2, "B", 2001, "", "")])
    cog = make_cog(search=search)
    interaction = make_interaction()
    await type(cog).movie_add.callback(cog, interaction, "matrix")
    assert "view" in interaction.followup.send.await_args.kwargs


async def test_movie_add_empty_text():
    cog = make_cog(search=make_search(enabled=False))
    interaction = make_interaction()
    await type(cog).movie_add.callback(cog, interaction, "   ")
    assert "Название" in interaction.followup.send.await_args.args[0]


async def test_add_entry_duplicate():
    container = make_container()
    container.add_movie.execute.return_value = AddMovieResult(status="duplicate")
    cog = make_cog(container)
    interaction = make_interaction()
    await cog.add_entry(interaction, MovieInfo(0, "Дубль", None, "", ""))
    assert "уже в вотчлисте" in interaction.followup.send.await_args.args[0]


async def test_add_entry_limit():
    container = make_container()
    container.add_movie.execute.return_value = AddMovieResult(status="limit")
    cog = make_cog(container)
    interaction = make_interaction()
    await cog.add_entry(interaction, MovieInfo(0, "Лишний", None, "", ""))
    assert "переполнен" in interaction.followup.send.await_args.args[0]


async def test_add_entry_success_posts_card():
    container = make_container()
    saved = make_entry("Начало", year=2010, id=5)
    container.add_movie.execute.return_value = AddMovieResult(status="ok", entry=saved)
    cog = make_cog(container)
    interaction = make_interaction()
    interaction.channel.send = AsyncMock(return_value=SimpleNamespace(id=777))
    interaction.channel.id = 100
    await cog.add_entry(interaction, MovieInfo(0, "Начало", 2010, "сон", ""))
    interaction.channel.send.assert_awaited_once()
    container.register_message.execute.assert_awaited_once()


# --- handle_vote ------------------------------------------------------------


async def test_handle_vote_gone():
    container = make_container()
    container.vote_movie.execute.return_value = VoteResult(status="gone")
    cog = make_cog(container)
    interaction = make_interaction()
    interaction.message = MagicMock()
    interaction.message.id = 200
    await cog.handle_vote(interaction, +1)
    assert "не в вотчлисте" in interaction.followup.send.await_args.args[0]


async def test_handle_vote_counts_updated():
    container = make_container()
    container.vote_movie.execute.return_value = VoteResult(status="ok", up=3, down=1, my_vote=1)
    cog = make_cog(container)
    interaction = make_interaction()
    interaction.message = MagicMock()
    interaction.message.id = 200
    embed = MagicMock()
    interaction.message.embeds = [embed]
    interaction.message.edit = AsyncMock()
    await cog.handle_vote(interaction, +1)
    interaction.message.edit.assert_awaited_once()
    assert "Учтён" in interaction.followup.send.await_args.args[0]


async def test_card_view_buttons_delegate():
    cog = make_cog()
    cog.handle_vote = AsyncMock()
    view = CinemaCardView(cog)
    interaction = make_interaction()
    await view.up_button.callback(interaction)
    await view.down_button.callback(interaction)
    assert cog.handle_vote.await_count == 2
    assert cog.handle_vote.await_args_list[0].args[1] == 1
    assert cog.handle_vote.await_args_list[1].args[1] == -1


async def test_rating_view_button_rates():
    cog = make_cog()
    cog.handle_rate = AsyncMock()
    view = CinemaRatingView(cog)
    # найдём кнопку оценки «7»
    button = next(c for c in view.children if getattr(c, "custom_id", "") == "cinema:rate:7")
    interaction = make_interaction()
    await button.callback(interaction)
    cog.handle_rate.assert_awaited_once_with(interaction, 7)


def test_rating_view_has_review_button():
    view = CinemaRatingView(make_cog())
    ids = [getattr(c, "custom_id", "") for c in view.children]
    assert "cinema:review" in ids


async def test_review_button_opens_modal():
    cog = make_cog()
    view = CinemaRatingView(cog)
    button = next(c for c in view.children if getattr(c, "custom_id", "") == "cinema:review")
    interaction = make_interaction()
    interaction.message = MagicMock(id=555)
    interaction.response.send_modal = AsyncMock()
    await button.callback(interaction)
    # открылась модалка отзыва с id карточки оценок
    modal = interaction.response.send_modal.await_args.args[0]
    assert modal.rating_message_id == 555


async def test_handle_review_ok_updates_footer():
    container = make_container()
    container.review_movie.execute.return_value = RateResult(
        status="ok", first_time=True, count=2, reviews=3
    )
    cog = make_cog(container)
    interaction = make_interaction()
    card = MagicMock()
    card.embeds = [MagicMock()]
    card.edit = AsyncMock()
    interaction.channel.fetch_message = AsyncMock(return_value=card)
    await cog.handle_review(interaction, 555, "моя рецензия")
    container.review_movie.execute.assert_awaited_once()
    card.edit.assert_awaited_once()
    assert "Отзыв записан" in interaction.followup.send.await_args.args[0]


async def test_handle_review_closed():
    container = make_container()
    container.review_movie.execute.return_value = RateResult(status="closed")
    cog = make_cog(container)
    interaction = make_interaction()
    await cog.handle_review(interaction, 555, "поздно")
    assert "уже закрыт" in interaction.followup.send.await_args.args[0]


async def test_finalize_posts_reviews_thread():
    container = make_container()
    container.list_reviews.execute.return_value = [
        MovieReview(user_id=1, score=9, text="шедевр"),
        MovieReview(user_id=2, score=None, text="без цифры, но норм"),
    ]
    cog = make_cog(container)
    summary = MagicMock()
    thread = MagicMock()
    thread.send = AsyncMock()
    summary.create_thread = AsyncMock(return_value=thread)
    entry = make_entry("Кино", id=5)
    await cog.forum.post_reviews_thread(summary, entry)
    summary.create_thread.assert_awaited_once()
    thread.send.assert_awaited()  # отзывы отправлены в ветку
    sent = thread.send.await_args.args[0]
    assert "шедевр" in sent and "без цифры" in sent


async def test_finalize_no_reviews_no_thread():
    container = make_container()
    container.list_reviews.execute.return_value = []
    cog = make_cog(container)
    summary = MagicMock()
    summary.create_thread = AsyncMock()
    await cog.forum.post_reviews_thread(summary, make_entry("Кино", id=5))
    summary.create_thread.assert_not_awaited()  # нет отзывов — нет ветки


# --- форум «золотой фонд» ---------------------------------------------------


def test_build_summary_embed():
    cog = make_cog()
    final = make_entry(
        "Начало",
        year=2010,
        id=5,
        poposya_score=9,
        poposya_review="сон во сне, уважаю",
        poster_url="http://p",
        watched_at=NOW,
    )
    embed = cog.forum.summary_embed(final, avg=8.4, count=3)
    names = [f.name for f in embed.fields]
    assert "⭐ Оценка сервера" in names
    assert any("Вердикт Попоси" in n for n in names)
    server = next(f for f in embed.fields if "Оценка сервера" in f.name)
    assert "8.4/10" in server.value and "3" in server.value


async def test_publish_forum_disabled_returns_none():
    cog = make_cog(settings=make_settings(cinema_forum_channel=0))
    link = await cog.forum.publish(make_entry("A", id=1), MagicMock())
    assert link is None


async def test_publish_forum_creates_post_and_ratings():
    container = make_container()
    container.list_ratings.execute.return_value = [
        MovieReview(user_id=1, score=9, text="шедевр"),
        MovieReview(user_id=2, score=7, text=""),  # только цифра
    ]
    cog = make_cog(container, settings=make_settings(cinema_forum_channel=555))
    forum = MagicMock(spec=discord.ForumChannel)
    thread = MagicMock()
    thread.mention = "<#777>"
    thread.send = AsyncMock()
    forum.create_thread = AsyncMock(
        return_value=SimpleNamespace(thread=thread, message=MagicMock())
    )
    cog.bot.get_channel = MagicMock(return_value=forum)

    link = await cog.forum.publish(make_entry("Начало", id=5), MagicMock())
    assert link == "<#777>"
    forum.create_thread.assert_awaited_once()
    thread.send.assert_awaited()  # рецензии + строка «без рецензии»
    posted = " ".join(c.args[0] for c in thread.send.await_args_list)
    assert "шедевр" in posted
    assert "Оценили без рецензии" in posted and "<@2> 7/10" in posted


async def test_publish_forum_text_channel_fallback():
    container = make_container()
    container.list_ratings.execute.return_value = []
    cog = make_cog(container, settings=make_settings(cinema_forum_channel=555))
    text = MagicMock(spec=discord.TextChannel)
    msg = MagicMock()
    msg.jump_url = "http://jump/1"
    msg.create_thread = AsyncMock(return_value=MagicMock())
    text.send = AsyncMock(return_value=msg)
    cog.bot.get_channel = MagicMock(return_value=text)
    link = await cog.forum.publish(make_entry("A", id=5), MagicMock())
    assert link == "http://jump/1"
    text.send.assert_awaited_once()


async def test_publish_forum_wrong_type_returns_none():
    cog = make_cog(settings=make_settings(cinema_forum_channel=555))
    cog.bot.get_channel = MagicMock(return_value=MagicMock())  # не форум и не текст
    link = await cog.forum.publish(make_entry("A", id=5), MagicMock())
    assert link is None


async def test_finalize_sends_pointer_when_forum_used():
    container = make_container()
    final = make_entry("Начало", id=5, channel_id=100, status="rating")
    container.get_movie.execute.return_value = final
    container.finalize_rating.execute.return_value = FinalizeResult(entry=final, avg=8.0, count=2)
    cog = make_cog(container, settings=make_settings(cinema_forum_channel=555))
    # make_cog передаёт chat=None -> AI-вердикт пропускается
    cog.service.disable_message = AsyncMock()
    cog.forum.publish = AsyncMock(return_value="<#777>")
    watch = MagicMock()
    watch.send = AsyncMock()
    cog.bot.get_channel = MagicMock(return_value=watch)

    await cog.service.finalize_rating(5)
    cog.forum.publish.assert_awaited_once()
    # в канал просмотра ушёл короткий указатель на форум
    pointer = watch.send.await_args.args[0]
    assert "золотом фонде" in pointer and "<#777>" in pointer


# --- /movie list / top / remove / watched -----------------------------------


async def test_movie_list_empty():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).movie_list.callback(cog, interaction)
    assert "пуст" in interaction.followup.send.await_args.args[0]


async def test_movie_list_ranked():
    container = make_container()
    container.list_watchlist.execute.return_value = [
        (make_entry("A", year=2000, id=1), 5, 1),
        (make_entry("B", id=2), 2, 0),
    ]
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).movie_list.callback(cog, interaction)
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert "Вотчлист (2)" in embed.title


async def test_movie_top_empty():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).movie_top.callback(cog, interaction)
    assert "ничего не посмотрели" in interaction.followup.send.await_args.args[0]


async def test_movie_top_lists():
    container = make_container()
    container.top_watched.execute.return_value = [
        make_entry(
            "Топ",
            year=2010,
            id=1,
            status="watched",
            avg_score=9.0,
            ratings_count=5,
            poposya_score=8,
        ),
    ]
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).movie_top.callback(cog, interaction)
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert "Золотой фонд" in embed.title


async def test_movie_remove_needs_valid_choice():
    cog = make_cog()
    interaction = make_interaction()
    await type(cog).movie_remove.callback(cog, interaction, "не-число")
    assert "из подсказок" in interaction.followup.send.await_args.args[0]


async def test_movie_remove_ok():
    container = make_container()
    entry = make_entry("Убрать", id=5, message_id=0)
    container.remove_movie.execute.return_value = ("ok", entry)
    cog = make_cog(container)
    interaction = make_interaction()
    interaction.user.guild_permissions = SimpleNamespace(administrator=True)
    await type(cog).movie_remove.callback(cog, interaction, "5")
    assert "Убрала" in interaction.followup.send.await_args.args[0]


async def test_movie_remove_forbidden():
    container = make_container()
    container.remove_movie.execute.return_value = ("forbidden", None)
    cog = make_cog(container)
    interaction = make_interaction()
    interaction.user.guild_permissions = SimpleNamespace(administrator=False)
    await type(cog).movie_remove.callback(cog, interaction, "5")
    assert "предложивший или администратор" in interaction.followup.send.await_args.args[0]


async def test_movie_watched_not_found():
    container = make_container()
    container.open_rating.execute.return_value = None
    cog = make_cog(container)
    interaction = make_interaction()
    await type(cog).movie_watched.callback(cog, interaction, "5")
    assert "не в вотчлисте или уже" in interaction.followup.send.await_args.args[0]
