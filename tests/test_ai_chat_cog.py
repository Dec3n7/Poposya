"""AIChatCog: адресация к боту, очистка контента, сбор истории, обработка
сообщения (ответ/оскорбление/ошибка) и комментарий к включённому треку."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from src.application.ai_chat.mood import MoodTracker
from src.application.ai_chat.service import ChatReply
from src.application.relationship.use_cases import AwardResult, RankInfo, SurveyData
from src.domain.ai_chat.exceptions import AIProviderError
from src.domain.music.events import TrackStarted
from src.infrastructure.discord.cogs.ai_chat import AIChatCog
from src.infrastructure.events.in_memory_bus import InMemoryEventBus

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


class Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def make_award(**over):
    base = dict(
        points=10,
        level=2,
        role_index=0,
        previous_role_index=0,
        point_awarded=True,
        is_exclusive=False,
        became_exclusive=False,
        returning_after_absence=False,
        user_notes="",
        survey=SurveyData(),
        recent_summaries=(),
    )
    base.update(over)
    return AwardResult(**base)


def make_settings(**over):
    base = dict(
        bot_insult_words=["дурак", "тупая"],
        ai_context_messages=25,
        ai_notes_update_every=10,
        ai_event_comment_chance=0.5,
        ai_event_comment_cooldown=900,
        main_channel="общий",
        ai_passive_enabled=False,
        ai_passive_only_main_channel=True,
        ai_passive_min_users=2,
        ai_passive_max_messages=20,
        ai_passive_debounce_seconds=45,
        ai_passive_cooldown_minutes=12,
        ai_passive_confidence_min=0.7,
    )
    base.update(over)
    return SimpleNamespace(**base)


def make_service(reply=None):
    svc = MagicMock()
    svc.respond = AsyncMock(
        return_value=reply or ChatReply(text="ответ", rate_limited=False, award=make_award())
    )
    svc.summarize_dialog = AsyncMock()
    svc.refresh_notes = AsyncMock()
    svc.comment_on_event = AsyncMock(return_value="крутой трек")
    svc.maybe_chime = AsyncMock(return_value=None)
    svc.get_rank = AsyncMock(
        return_value=RankInfo(
            points=10,
            level=2,
            role_index=0,
            is_exclusive=False,
            frozen=False,
            next_threshold=100,
            survey=SurveyData(),
        )
    )
    return svc


def make_cog(service=None, settings=None):
    bot = MagicMock()
    bot.user = SimpleNamespace(id=999)
    role_sync = MagicMock(sync_member=AsyncMock())
    return AIChatCog(
        bot,
        service or make_service(),
        settings or make_settings(),
        role_sync,
        InMemoryEventBus(),
        MoodTracker(),
    )


def make_message(content="привет", author_id=1, mentions_bot=True, guild_id=10):
    message = MagicMock()
    message.author = SimpleNamespace(id=author_id, bot=False, display_name="Гость")
    message.guild = SimpleNamespace(id=guild_id)
    message.channel = MagicMock()
    message.channel.name = "общий"
    message.channel.typing = MagicMock(return_value=Typing())
    message.channel.id = 100
    message.content = content
    message.mentions = [SimpleNamespace(id=999)] if mentions_bot else []
    message.reference = None
    message.reply = AsyncMock()
    return message


# --- _is_addressed_to_bot / _clean_content ---------------------------------


def test_is_addressed_by_mention():
    cog = make_cog()
    msg = make_message(mentions_bot=True)
    msg.mentions = [cog.bot.user]
    assert cog._is_addressed_to_bot(msg) is True


def test_is_addressed_by_reply():
    import discord

    cog = make_cog()
    msg = make_message(mentions_bot=False)
    replied = MagicMock(spec=discord.Message)
    replied.author = SimpleNamespace(id=999)
    msg.reference = SimpleNamespace(resolved=replied)
    assert cog._is_addressed_to_bot(msg) is True


def test_not_addressed():
    cog = make_cog()
    msg = make_message(mentions_bot=False)
    assert cog._is_addressed_to_bot(msg) is False


def test_clean_content_strips_mention():
    cog = make_cog()
    msg = make_message(content="<@999> привет  ")
    assert cog._clean_content(msg) == "привет"


# --- on_message -------------------------------------------------------------


async def test_on_message_ignores_bots():
    cog = make_cog()
    msg = make_message()
    msg.author = SimpleNamespace(id=2, bot=True, display_name="B")
    await cog.on_message(msg)
    cog.service.respond.assert_not_awaited()


async def test_on_message_ignores_not_addressed():
    cog = make_cog()
    msg = make_message(mentions_bot=False)
    await cog.on_message(msg)
    cog.service.respond.assert_not_awaited()


async def test_on_message_replies_and_bumps_mood():
    cog = make_cog()
    msg = make_message()
    msg.channel.history = MagicMock(return_value=_empty_aiter())
    await cog.on_message(msg)
    cog.service.respond.assert_awaited_once()
    msg.reply.assert_awaited_once()
    assert cog.mood.get(10) == 51  # +1 за ответ


async def test_on_message_insult_lowers_mood():
    cog = make_cog()
    msg = make_message(content="<@999> ты тупая")
    msg.channel.history = MagicMock(return_value=_empty_aiter())
    await cog.on_message(msg)
    # -5 за оскорбление, +1 за ответ = 46
    assert cog.mood.get(10) == 46


async def test_on_message_provider_error_sends_fallback():
    service = make_service()
    service.respond = AsyncMock(side_effect=AIProviderError("down"))
    cog = make_cog(service)
    msg = make_message()
    msg.channel.history = MagicMock(return_value=_empty_aiter())
    await cog.on_message(msg)
    msg.reply.assert_awaited_once()  # отправлен один из _ERROR_REPLIES


async def test_on_message_silent_when_module_off():
    """Мастер модуля выключен — на обращение к боту не отвечаем вовсе."""
    cog = make_cog(settings=make_settings(ai_chat_enabled=False))
    msg = make_message()
    await cog.on_message(msg)
    cog.service.respond.assert_not_awaited()


async def test_on_message_silent_when_reactive_off():
    """Подфлаг «ответы на обращения» выключен — молчим на упоминание."""
    cog = make_cog(settings=make_settings(ai_reactive=False))
    msg = make_message()
    await cog.on_message(msg)
    cog.service.respond.assert_not_awaited()


# --- _on_track_started ------------------------------------------------------


async def test_track_comment_quiet_survey_silent():
    service = make_service()
    service.get_rank = AsyncMock(
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
    cog = make_cog(service)
    event = TrackStarted(
        aggregate_id="10", guild_id=10, channel_id=100, title="Song", url="u", requested_by=1
    )
    await cog._on_track_started(event)
    service.comment_on_event.assert_not_awaited()


async def test_track_comment_zero_channel_skipped():
    cog = make_cog()
    event = TrackStarted(
        aggregate_id="10", guild_id=10, channel_id=0, title="Song", url="u", requested_by=1
    )
    await cog._on_track_started(event)
    cog.service.comment_on_event.assert_not_awaited()


async def test_track_comment_skipped_when_disabled():
    """Подфлаг «комментарии к трекам» выключен — реплику не генерируем."""
    cog = make_cog(settings=make_settings(ai_event_comments=False))
    event = TrackStarted(
        aggregate_id="10", guild_id=10, channel_id=100, title="Song", url="u", requested_by=1
    )
    await cog._on_track_started(event)
    cog.service.comment_on_event.assert_not_awaited()


async def test_track_comment_posts(monkeypatch):
    monkeypatch.setattr("src.infrastructure.discord.cogs.ai_chat.random.random", lambda: 0.0)
    service = make_service()
    cog = make_cog(service)
    channel = MagicMock()
    channel.send = AsyncMock()
    channel.guild = MagicMock()
    channel.guild.get_member.return_value = SimpleNamespace(display_name="Гость")
    cog.bot.get_channel.return_value = channel
    event = TrackStarted(
        aggregate_id="10", guild_id=10, channel_id=100, title="Nirvana", url="u", requested_by=1
    )
    await cog._on_track_started(event)
    service.comment_on_event.assert_awaited_once()
    channel.send.assert_awaited_once()


async def test_track_comment_respects_cooldown(monkeypatch):
    monkeypatch.setattr("src.infrastructure.discord.cogs.ai_chat.random.random", lambda: 0.0)
    service = make_service()
    cog = make_cog(service)
    cog._event_cooldowns[100] = 10**9  # «только что» комментировали
    monkeypatch.setattr("src.infrastructure.discord.cogs.ai_chat.time.monotonic", lambda: 10**9)
    event = TrackStarted(
        aggregate_id="10", guild_id=10, channel_id=100, title="Song", url="u", requested_by=1
    )
    await cog._on_track_started(event)
    service.comment_on_event.assert_not_awaited()


# --- чистка памяти ----------------------------------------------------------


def test_prune_event_cooldowns_drops_expired(monkeypatch):
    cog = make_cog()  # ai_event_comment_cooldown = 900
    monkeypatch.setattr("src.infrastructure.discord.cogs.ai_chat.time.monotonic", lambda: 10_000.0)
    cog._event_cooldowns[1] = 10_000.0 - 5000  # старше кулдауна — выбросить
    cog._event_cooldowns[2] = 10_000.0 - 100  # свежий — оставить
    cog._prune_event_cooldowns()
    assert 1 not in cog._event_cooldowns
    assert 2 in cog._event_cooldowns


async def test_sweep_loop_summarizes_evicted_sessions(monkeypatch):
    service = make_service()
    service.evict_stale_sessions = MagicMock(
        return_value=[(10, 1, "Аня", [("a", "b"), ("c", "d")])]
    )
    cog = make_cog(service)
    # прогоняем ровно одну итерацию цикла: sleep -> работа -> отмена
    monkeypatch.setattr(
        "src.infrastructure.discord.cogs.ai_chat.asyncio.sleep",
        AsyncMock(side_effect=[None, asyncio.CancelledError()]),
    )
    with pytest.raises(asyncio.CancelledError):
        await cog._sweep_loop()
    service.evict_stale_sessions.assert_called_once()
    # summarize запускается через _spawn (фоновая задача) — фиксируем факт вызова
    service.summarize_dialog.assert_called_once_with(10, 1, "Аня", [("a", "b"), ("c", "d")], ANY)
    for task in list(cog._background):
        task.cancel()


async def _empty_aiter():
    return
    yield  # pragma: no cover


# --- пассивное вклинивание -------------------------------------------------


def _fmsg(uid, text, bot=False):
    return SimpleNamespace(
        author=SimpleNamespace(id=uid, bot=bot, display_name=f"U{uid}"), content=text
    )


async def _msgs_aiter(msgs):
    for m in msgs:
        yield m


def _chan_with_msgs(msgs):
    ch = MagicMock()
    ch.history = MagicMock(return_value=_msgs_aiter(msgs))
    ch.send = AsyncMock()
    return ch


def test_passive_disabled_no_schedule():
    cog = make_cog()  # ai_passive_enabled=False по умолчанию
    cog._consider_passive(make_message(mentions_bot=False))
    assert len(cog._chime_scheduler) == 0


async def test_passive_schedules_when_enabled():
    cog = make_cog(settings=make_settings(ai_passive_enabled=True))
    cog._consider_passive(make_message(mentions_bot=False))
    assert len(cog._chime_scheduler) == 1
    cog._chime_scheduler.cancel_all()


def test_passive_skips_non_main_channel():
    cog = make_cog(settings=make_settings(ai_passive_enabled=True, main_channel="другой"))
    cog._consider_passive(make_message(mentions_bot=False))  # канал «общий» != «другой»
    assert len(cog._chime_scheduler) == 0


async def test_try_chime_posts_when_decided():
    svc = make_service()
    svc.maybe_chime = AsyncMock(return_value="колкая реплика")
    cog = make_cog(service=svc, settings=make_settings(ai_passive_enabled=True))
    channel = _chan_with_msgs([_fmsg(1, "sekiro хорош"), _fmsg(2, "согласен")])
    cog.bot.get_channel = MagicMock(return_value=channel)
    await cog._try_chime(10, 100)
    channel.send.assert_awaited_once()
    assert channel.send.await_args.args[0] == "колкая реплика"
    assert 100 in cog._chime_cooldowns  # кулдаун выставлен


async def test_try_chime_skips_below_min_users():
    svc = make_service()
    cog = make_cog(service=svc, settings=make_settings(ai_passive_enabled=True))
    channel = _chan_with_msgs([_fmsg(1, "один я тут")])  # один человек
    cog.bot.get_channel = MagicMock(return_value=channel)
    await cog._try_chime(10, 100)
    svc.maybe_chime.assert_not_called()  # до модели не дошли
    channel.send.assert_not_awaited()


async def test_try_chime_silent_when_model_declines():
    svc = make_service()
    svc.maybe_chime = AsyncMock(return_value=None)  # решила промолчать
    cog = make_cog(service=svc, settings=make_settings(ai_passive_enabled=True))
    channel = _chan_with_msgs([_fmsg(1, "a"), _fmsg(2, "b")])
    cog.bot.get_channel = MagicMock(return_value=channel)
    await cog._try_chime(10, 100)
    channel.send.assert_not_awaited()
    assert 100 not in cog._chime_cooldowns
