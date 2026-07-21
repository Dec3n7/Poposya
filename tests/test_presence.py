"""PresenceService: музыка перебивает «жизнь», дедуп, ротация занятий."""

from unittest.mock import AsyncMock, MagicMock

import discord

from src.infrastructure.discord.presence import _ACTIVITIES, PresenceService


def make_service():
    bot = MagicMock()
    bot.change_presence = AsyncMock()
    return PresenceService(bot, rotate_minutes=30), bot


async def test_now_playing_shows_listening():
    svc, bot = make_service()
    await svc.set_now_playing("Song A")
    activity = bot.change_presence.await_args.kwargs["activity"]
    assert activity.type == discord.ActivityType.listening
    assert activity.name == "Song A"


async def test_same_track_not_reapplied():
    svc, bot = make_service()
    await svc.set_now_playing("Song A")
    await svc.set_now_playing("Song A")  # тот же трек — API не дёргаем повторно
    assert bot.change_presence.await_count == 1


async def test_music_stops_switches_to_life_activity():
    svc, bot = make_service()
    await svc.set_now_playing("Song A")  # играет
    await svc.set_now_playing(None)  # замолчала -> сразу занятие из жизни
    last = bot.change_presence.await_args.kwargs["activity"]
    # ушли от играющего трека к какому-то занятию (оно может быть и «слушает
    # lo-fi» — это тоже её жизнь, поэтому проверяем смену имени, а не тип)
    assert last.name and last.name != "Song A"
    names = {text for _, text in _ACTIVITIES}
    assert last.name in names


async def test_random_activity_is_from_her_list():
    svc, _ = make_service()
    names = {text for _, text in _ACTIVITIES}
    for _ in range(20):
        activity = svc._random_activity()
        assert activity.name in names  # только её канон, без случайщины


async def test_custom_activities_use_custom_type():
    # свободный текст («читает Мураками») — CustomActivity, не Game
    svc, _ = make_service()
    custom_texts = {text for atype, text in _ACTIVITIES if atype is None}
    seen_custom = False
    for _ in range(200):
        a = svc._random_activity()
        if a.name in custom_texts:
            assert isinstance(a, discord.CustomActivity)
            seen_custom = True
    assert seen_custom  # за 200 бросков хоть раз выпал свободный текст


async def test_presence_none_in_service_is_safe():
    # музыкальный сервис без PresenceService (в тестах) не должен падать
    from types import SimpleNamespace

    from src.infrastructure.discord.cogs.music.service import MusicPlayerService

    container = SimpleNamespace(
        settings=SimpleNamespace(),
        audio_source=MagicMock(),
        event_bus=MagicMock(),
    )
    svc = MusicPlayerService(MagicMock(), container)  # presence=None по умолчанию
    await svc.refresh_presence()  # не падает, просто ничего не делает


# --- P3: пул занятий из персоны ----------------------------------------------


async def test_pool_defaults_to_builtin_canon():
    svc, _bot = make_service()
    assert svc._pool() is _ACTIVITIES


async def test_pool_uses_persona_lines():
    svc, _bot = make_service()
    svc.set_lines_provider(lambda: ["читает Мураками", "гуляет"])
    assert svc._pool() == [(None, "читает Мураками"), (None, "гуляет")]
    activity = svc._random_activity()
    assert isinstance(activity, discord.CustomActivity)
    assert activity.name in ("читает Мураками", "гуляет")


async def test_pool_falls_back_when_provider_empty_or_broken():
    svc, _bot = make_service()
    svc.set_lines_provider(lambda: [])
    assert svc._pool() is _ACTIVITIES

    def broken() -> list[str]:
        raise RuntimeError("боль")

    svc.set_lines_provider(broken)
    assert svc._pool() is _ACTIVITIES


async def test_refresh_applies_when_music_silent():
    svc, bot = make_service()
    svc.set_lines_provider(lambda: ["один статус"])
    await svc.refresh()
    activity = bot.change_presence.await_args.kwargs["activity"]
    assert activity.name == "один статус"


async def test_refresh_noop_while_music_playing():
    svc, bot = make_service()
    svc.set_lines_provider(lambda: ["статус"])
    await svc.set_now_playing("трек")
    count_before = bot.change_presence.await_count
    await svc.refresh()  # музыка владеет статусом — не трогаем
    assert bot.change_presence.await_count == count_before
