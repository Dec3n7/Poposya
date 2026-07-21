"""CinemaService: закрытие опроса киновечера, напоминание, карточка/итоги
оценок, вердикт Попоси. Все внешние Discord-вызовы — через AsyncMock, форум
и планировщик — через фейки/MagicMock (сервис их не создаёт сам)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.application.ai_chat.mood import MoodTracker
from src.application.cinema.use_cases import (
    ClosePollResult,
    FinalizeResult,
    PendingCinema,
)
from src.domain.cinema.entities import MovieEntry, MovieNight
from src.infrastructure.discord.cogs.cinema.service import CinemaService
from tests.cog_fakes import http_error

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def make_entry(**over) -> MovieEntry:
    base = dict(
        guild_id=10,
        title="Фильм",
        added_by=1,
        added_at=NOW,
        id=1,
        channel_id=100,
    )
    base.update(over)
    return MovieEntry(**base)


def make_night(**over) -> MovieNight:
    base = dict(
        guild_id=10,
        created_by=1,
        scheduled_at=NOW + timedelta(hours=2),
        poll_ends_at=NOW,
        id=5,
        channel_id=100,
        poll_message_id=200,
    )
    base.update(over)
    return MovieNight(**base)


def make_cinema_container():
    c = SimpleNamespace()
    c.close_poll = SimpleNamespace(execute=AsyncMock())
    c.register_message = SimpleNamespace(execute=AsyncMock())
    c.list_pending = SimpleNamespace(execute=AsyncMock())
    c.get_movie = SimpleNamespace(execute=AsyncMock())
    c.finalize_rating = SimpleNamespace(execute=AsyncMock())
    return c


def make_service(cinema=None, bot=None, chat=None, forum=None):
    bot = bot or MagicMock()
    scheduler = MagicMock()
    forum = forum or MagicMock()
    return CinemaService(
        cinema or make_cinema_container(),
        bot,
        chat,
        MoodTracker(),
        scheduler,
        forum,
        watched_view=MagicMock(),
        rating_view=MagicMock(),
    ), scheduler


# --- disable_message ---------------------------------------------------------


async def test_disable_message_noop_without_ids():
    service, _ = make_service()
    await service.disable_message(0, 0)  # не падает, ничего не вызывает
    service._bot.get_channel.assert_not_called()


async def test_disable_message_noop_when_channel_missing():
    bot = MagicMock()
    bot.get_channel.return_value = None
    service, _ = make_service(bot=bot)
    await service.disable_message(100, 200)


async def test_disable_message_edits_view_off():
    bot = MagicMock()
    channel = MagicMock()
    message = MagicMock()
    message.edit = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=message)
    bot.get_channel.return_value = channel
    service, _ = make_service(bot=bot)
    await service.disable_message(100, 200)
    message.edit.assert_awaited_once_with(view=None)


async def test_disable_message_swallows_http_exception():
    bot = MagicMock()
    channel = MagicMock()
    channel.fetch_message = AsyncMock(side_effect=http_error())
    bot.get_channel.return_value = channel
    service, _ = make_service(bot=bot)
    await service.disable_message(100, 200)  # не падает


# --- close_poll ----------------------------------------------------------------


async def test_close_poll_gone_status_returns_early():
    cinema = make_cinema_container()
    cinema.close_poll.execute.return_value = ClosePollResult(status="gone")
    service, _ = make_service(cinema=cinema)
    await service.close_poll(5)
    service._bot.get_channel.assert_not_called()


async def test_close_poll_channel_missing_returns():
    cinema = make_cinema_container()
    night = make_night()
    cinema.close_poll.execute.return_value = ClosePollResult(status="no_votes", night=night)
    bot = MagicMock()
    bot.get_channel.return_value = None
    service, _ = make_service(cinema=cinema, bot=bot)
    await service.close_poll(5)


async def test_close_poll_no_votes_sends_cancellation():
    cinema = make_cinema_container()
    night = make_night()
    cinema.close_poll.execute.return_value = ClosePollResult(status="no_votes", night=night)
    bot = MagicMock()
    channel = MagicMock()
    channel.send = AsyncMock()
    channel.fetch_message = AsyncMock(side_effect=http_error())  # disable_message: не важно
    bot.get_channel.return_value = channel
    service, _ = make_service(cinema=cinema, bot=bot)
    await service.close_poll(5)
    channel.send.assert_awaited_once()
    assert "отменяется" in channel.send.await_args.args[0]


async def test_close_poll_no_votes_send_http_exception_ignored():
    cinema = make_cinema_container()
    night = make_night()
    cinema.close_poll.execute.return_value = ClosePollResult(status="no_votes", night=night)
    bot = MagicMock()
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=http_error())
    channel.fetch_message = AsyncMock(side_effect=http_error())  # disable_message: не важно
    bot.get_channel.return_value = channel
    service, _ = make_service(cinema=cinema, bot=bot)
    await service.close_poll(5)  # не падает


async def test_close_poll_winner_without_chat_announces_and_schedules():
    cinema = make_cinema_container()
    night = make_night()
    winner = make_entry(title="Победитель", year=2020)
    cinema.close_poll.execute.return_value = ClosePollResult(
        status="winner", night=night, winner=winner, votes={1: 3, 2: 1}
    )
    bot = MagicMock()
    channel = MagicMock()
    message = MagicMock()
    message.id = 999
    channel.send = AsyncMock(return_value=message)
    channel.fetch_message = AsyncMock(side_effect=http_error())  # disable_message: не важно
    bot.get_channel.return_value = channel
    service, scheduler = make_service(cinema=cinema, bot=bot)
    await service.close_poll(5)
    channel.send.assert_awaited_once()
    assert channel.send.await_args.kwargs["content"] is None  # без AI-комментария
    cinema.register_message.execute.assert_awaited_once_with("winner", night.id, channel.id, 999)
    scheduler.schedule.assert_called_once()
    assert scheduler.schedule.call_args.args[0] == f"remind:{night.id}"


async def test_close_poll_winner_with_poster_sets_thumbnail():
    cinema = make_cinema_container()
    night = make_night()
    winner = make_entry(title="Победитель", poster_url="http://poster")
    cinema.close_poll.execute.return_value = ClosePollResult(
        status="winner", night=night, winner=winner, votes={}
    )
    bot = MagicMock()
    channel = MagicMock()
    message = MagicMock()
    message.id = 999
    channel.send = AsyncMock(return_value=message)
    channel.fetch_message = AsyncMock(side_effect=http_error())  # disable_message: не важно
    bot.get_channel.return_value = channel
    service, _ = make_service(cinema=cinema, bot=bot)
    await service.close_poll(5)
    embed = channel.send.await_args.kwargs["embed"]
    assert embed.thumbnail.url == "http://poster"


async def test_close_poll_winner_with_chat_comment():
    cinema = make_cinema_container()
    night = make_night()
    winner = make_entry(title="Победитель")
    cinema.close_poll.execute.return_value = ClosePollResult(
        status="winner", night=night, winner=winner, votes={1: 2}
    )
    bot = MagicMock()
    channel = MagicMock()
    message = MagicMock()
    message.id = 999
    channel.send = AsyncMock(return_value=message)
    channel.fetch_message = AsyncMock(side_effect=http_error())  # disable_message: не важно
    bot.get_channel.return_value = channel
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(return_value="Отличный выбор.")
    service, _ = make_service(cinema=cinema, bot=bot, chat=chat)
    await service.close_poll(5)
    assert channel.send.await_args.kwargs["content"] == "Отличный выбор."


async def test_close_poll_winner_chat_failure_falls_back_to_empty_comment():
    cinema = make_cinema_container()
    night = make_night()
    winner = make_entry(title="Победитель")
    cinema.close_poll.execute.return_value = ClosePollResult(
        status="winner", night=night, winner=winner, votes={}
    )
    bot = MagicMock()
    channel = MagicMock()
    message = MagicMock()
    message.id = 999
    channel.send = AsyncMock(return_value=message)
    channel.fetch_message = AsyncMock(side_effect=http_error())  # disable_message: не важно
    bot.get_channel.return_value = channel
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(side_effect=RuntimeError("boom"))
    service, _ = make_service(cinema=cinema, bot=bot, chat=chat)
    await service.close_poll(5)
    assert channel.send.await_args.kwargs["content"] is None


async def test_close_poll_winner_send_failure_skips_register_and_schedule():
    cinema = make_cinema_container()
    night = make_night()
    winner = make_entry(title="Победитель")
    cinema.close_poll.execute.return_value = ClosePollResult(
        status="winner", night=night, winner=winner, votes={}
    )
    bot = MagicMock()
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=http_error())
    channel.fetch_message = AsyncMock(side_effect=http_error())  # disable_message: не важно
    bot.get_channel.return_value = channel
    service, scheduler = make_service(cinema=cinema, bot=bot)
    await service.close_poll(5)
    cinema.register_message.execute.assert_not_awaited()
    scheduler.schedule.assert_not_called()


# --- remind --------------------------------------------------------------------


async def test_remind_cancelled_night_is_skipped():
    cinema = make_cinema_container()
    cinema.list_pending.execute.return_value = PendingCinema(polls=[], scheduled=[], ratings=[])
    service, _ = make_service(cinema=cinema)
    await service.remind(make_night())
    cinema.get_movie.execute.assert_not_awaited()


async def test_remind_missing_winner_returns():
    cinema = make_cinema_container()
    night = make_night()
    cinema.list_pending.execute.return_value = PendingCinema(
        polls=[], scheduled=[night], ratings=[]
    )
    cinema.get_movie.execute.return_value = None
    service, _ = make_service(cinema=cinema)
    await service.remind(night)
    service._bot.get_channel.assert_not_called()


async def test_remind_channel_missing_returns():
    cinema = make_cinema_container()
    night = make_night()
    cinema.list_pending.execute.return_value = PendingCinema(
        polls=[], scheduled=[night], ratings=[]
    )
    cinema.get_movie.execute.return_value = make_entry(title="X")
    bot = MagicMock()
    bot.get_channel.return_value = None
    service, _ = make_service(cinema=cinema, bot=bot)
    await service.remind(night)


async def test_remind_sends_message():
    cinema = make_cinema_container()
    night = make_night()
    cinema.list_pending.execute.return_value = PendingCinema(
        polls=[], scheduled=[night], ratings=[]
    )
    cinema.get_movie.execute.return_value = make_entry(title="Идущий")
    bot = MagicMock()
    channel = MagicMock()
    channel.send = AsyncMock()
    bot.get_channel.return_value = channel
    service, _ = make_service(cinema=cinema, bot=bot)
    await service.remind(night)
    channel.send.assert_awaited_once()
    assert "Идущий" in channel.send.await_args.args[0]


async def test_remind_send_http_exception_ignored():
    cinema = make_cinema_container()
    night = make_night()
    cinema.list_pending.execute.return_value = PendingCinema(
        polls=[], scheduled=[night], ratings=[]
    )
    cinema.get_movie.execute.return_value = make_entry(title="X")
    bot = MagicMock()
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=http_error())
    bot.get_channel.return_value = channel
    service, _ = make_service(cinema=cinema, bot=bot)
    await service.remind(night)  # не падает


# --- post_rating_message ---------------------------------------------------------


async def test_post_rating_message_success_registers_and_schedules():
    cinema = make_cinema_container()
    entry = make_entry(rating_ends_at=NOW + timedelta(hours=6), poster_url="http://poster")
    channel = MagicMock()
    message = MagicMock()
    message.id = 321
    message.channel = SimpleNamespace(id=100)
    channel.send = AsyncMock(return_value=message)
    service, scheduler = make_service(cinema=cinema)
    await service.post_rating_message(channel, entry)
    cinema.register_message.execute.assert_awaited_once_with("rating", entry.id, 100, 321)
    scheduler.schedule.assert_called_once()
    assert scheduler.schedule.call_args.args[0] == f"rating:{entry.id}"


async def test_post_rating_message_without_poster_skips_thumbnail():
    cinema = make_cinema_container()
    entry = make_entry(rating_ends_at=NOW + timedelta(hours=6), poster_url="")
    channel = MagicMock()
    message = MagicMock()
    message.channel = SimpleNamespace(id=100)
    channel.send = AsyncMock(return_value=message)
    service, _ = make_service(cinema=cinema)
    await service.post_rating_message(channel, entry)
    channel.send.assert_awaited_once()


async def test_post_rating_message_send_failure_skips_register():
    cinema = make_cinema_container()
    entry = make_entry(rating_ends_at=NOW + timedelta(hours=6))
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=http_error())
    service, scheduler = make_service(cinema=cinema)
    await service.post_rating_message(channel, entry)
    cinema.register_message.execute.assert_not_awaited()
    scheduler.schedule.assert_not_called()


# --- _poposya_verdict ------------------------------------------------------------


async def test_poposya_verdict_no_chat_returns_empty():
    service, _ = make_service(chat=None)
    score, review = await service._poposya_verdict(make_entry(), 10)
    assert (score, review) == (None, "")


async def test_poposya_verdict_parses_score():
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(return_value="8/10 — неплохо, но не шедевр.")
    service, _ = make_service(chat=chat)
    score, review = await service._poposya_verdict(make_entry(), 10)
    assert score == 8
    assert "неплохо" in review


async def test_poposya_verdict_clamps_score_to_ten():
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(return_value="15/10 — перебор с оценкой.")
    service, _ = make_service(chat=chat)
    score, _review = await service._poposya_verdict(make_entry(), 10)
    assert score == 10


async def test_poposya_verdict_no_match_leaves_score_none():
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(return_value="Без числа вообще.")
    service, _ = make_service(chat=chat)
    score, review = await service._poposya_verdict(make_entry(), 10)
    assert score is None
    assert review == "Без числа вообще."


async def test_poposya_verdict_chat_failure_returns_empty():
    chat = MagicMock()
    chat.freeform_remark = AsyncMock(side_effect=RuntimeError("boom"))
    service, _ = make_service(chat=chat)
    score, review = await service._poposya_verdict(make_entry(), 10)
    assert (score, review) == (None, "")


# --- finalize_rating ---------------------------------------------------------------


async def test_finalize_rating_missing_entry_returns():
    cinema = make_cinema_container()
    cinema.get_movie.execute.return_value = None
    service, _ = make_service(cinema=cinema)
    await service.finalize_rating(1)
    cinema.finalize_rating.execute.assert_not_awaited()


async def test_finalize_rating_wrong_status_returns():
    cinema = make_cinema_container()
    cinema.get_movie.execute.return_value = make_entry(status="watched")
    service, _ = make_service(cinema=cinema)
    await service.finalize_rating(1)
    cinema.finalize_rating.execute.assert_not_awaited()


async def test_finalize_rating_finalize_returns_none_stops():
    cinema = make_cinema_container()
    cinema.get_movie.execute.return_value = make_entry(status="rating")
    cinema.finalize_rating.execute.return_value = None
    forum = MagicMock()
    service, _ = make_service(cinema=cinema, forum=forum)
    await service.finalize_rating(1)
    forum.summary_embed.assert_not_called()


async def test_finalize_rating_forum_publish_success_sends_pointer():
    cinema = make_cinema_container()
    entry = make_entry(status="rating", rating_message_id=555)
    cinema.get_movie.execute.return_value = entry
    final = make_entry(status="watched", title="Итог")
    cinema.finalize_rating.execute.return_value = FinalizeResult(entry=final, avg=8.5, count=3)
    forum = MagicMock()
    forum.summary_embed.return_value = MagicMock()
    forum.publish = AsyncMock(return_value="https://discord/forum/thread")
    bot = MagicMock()
    channel = MagicMock()
    channel.send = AsyncMock()
    channel.fetch_message = AsyncMock(side_effect=http_error())  # disable_message: не важно
    bot.get_channel.return_value = channel
    service, _ = make_service(cinema=cinema, bot=bot, forum=forum)
    await service.finalize_rating(1)
    channel.send.assert_awaited_once()
    assert "золотом фонде" in channel.send.await_args.args[0]
    forum.post_reviews_thread.assert_not_called()


async def test_finalize_rating_forum_publish_success_no_channel_still_returns():
    cinema = make_cinema_container()
    entry = make_entry(status="rating")
    cinema.get_movie.execute.return_value = entry
    final = make_entry(status="watched")
    cinema.finalize_rating.execute.return_value = FinalizeResult(entry=final, avg=None, count=0)
    forum = MagicMock()
    forum.summary_embed.return_value = MagicMock()
    forum.publish = AsyncMock(return_value="https://discord/forum/thread")
    bot = MagicMock()
    bot.get_channel.return_value = None
    service, _ = make_service(cinema=cinema, bot=bot, forum=forum)
    await service.finalize_rating(1)  # не падает, публикация всё равно случилась
    forum.publish.assert_awaited_once()


async def test_finalize_rating_forum_publish_send_http_exception_ignored():
    cinema = make_cinema_container()
    entry = make_entry(status="rating")
    cinema.get_movie.execute.return_value = entry
    final = make_entry(status="watched")
    cinema.finalize_rating.execute.return_value = FinalizeResult(entry=final, avg=7.0, count=1)
    forum = MagicMock()
    forum.summary_embed.return_value = MagicMock()
    forum.publish = AsyncMock(return_value="https://discord/forum/thread")
    bot = MagicMock()
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=http_error())
    bot.get_channel.return_value = channel
    service, _ = make_service(cinema=cinema, bot=bot, forum=forum)
    await service.finalize_rating(1)  # не падает


async def test_finalize_rating_fallback_no_channel_returns():
    cinema = make_cinema_container()
    entry = make_entry(status="rating")
    cinema.get_movie.execute.return_value = entry
    final = make_entry(status="watched")
    cinema.finalize_rating.execute.return_value = FinalizeResult(entry=final, avg=None, count=0)
    forum = MagicMock()
    forum.summary_embed.return_value = MagicMock()
    forum.publish = AsyncMock(return_value=None)  # форум недоступен
    bot = MagicMock()
    bot.get_channel.return_value = None
    service, _ = make_service(cinema=cinema, bot=bot, forum=forum)
    await service.finalize_rating(1)
    forum.post_reviews_thread.assert_not_called()


async def test_finalize_rating_fallback_posts_summary_and_reviews_thread():
    cinema = make_cinema_container()
    entry = make_entry(status="rating")
    cinema.get_movie.execute.return_value = entry
    final = make_entry(status="watched", title="Итог")
    cinema.finalize_rating.execute.return_value = FinalizeResult(entry=final, avg=6.0, count=2)
    forum = MagicMock()
    forum.summary_embed.return_value = MagicMock()
    forum.publish = AsyncMock(return_value=None)
    forum.post_reviews_thread = AsyncMock()
    bot = MagicMock()
    channel = MagicMock()
    summary_message = MagicMock()
    channel.send = AsyncMock(return_value=summary_message)
    bot.get_channel.return_value = channel
    service, _ = make_service(cinema=cinema, bot=bot, forum=forum)
    await service.finalize_rating(1)
    channel.send.assert_awaited_once()
    forum.post_reviews_thread.assert_awaited_once_with(summary_message, final)


async def test_finalize_rating_fallback_send_failure_skips_reviews_thread():
    cinema = make_cinema_container()
    entry = make_entry(status="rating")
    cinema.get_movie.execute.return_value = entry
    final = make_entry(status="watched")
    cinema.finalize_rating.execute.return_value = FinalizeResult(entry=final, avg=None, count=0)
    forum = MagicMock()
    forum.summary_embed.return_value = MagicMock()
    forum.publish = AsyncMock(return_value=None)
    forum.post_reviews_thread = AsyncMock()
    bot = MagicMock()
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=http_error())
    bot.get_channel.return_value = channel
    service, _ = make_service(cinema=cinema, bot=bot, forum=forum)
    await service.finalize_rating(1)
    forum.post_reviews_thread.assert_not_awaited()
