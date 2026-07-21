"""LyricsService: кэш текстов, префетч, эмбеды (plain/караоке), стоп/старт и
кнопка 📜 (toggle). Живой цикл (_live_loop) не гоняем — create_task мокаем."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.domain.music.entities import Track
from src.infrastructure.discord.cogs.music.lyrics import LyricsService
from src.infrastructure.discord.cogs.music.session import GuildMusicSession

SYNCED = "[00:00.00] строка один\n[00:05.00] строка два\n[00:10.00] строка три"


def make_track(vid="a", title="Песня"):
    return Track(
        video_id=vid, title=title, url="u", duration=200, requested_by=1, uploader="Artist"
    )


def make_settings():
    return SimpleNamespace(
        music_lyrics_offset=1.0, music_progress_interval=5, music_karaoke_ansi=True
    )


def make_service(client=None, session=None, spawn=None):
    client = client or SimpleNamespace(find_both=AsyncMock(return_value=(SYNCED, "плейн текст")))
    get_session = lambda gid: session
    spawn = spawn or (lambda coro: coro.close())
    return LyricsService(client, make_settings(), get_session, spawn)


# --- кэш и префетч ----------------------------------------------------------


async def test_get_caches_result():
    client = SimpleNamespace(find_both=AsyncMock(return_value=(SYNCED, "plain")))
    svc = make_service(client=client)
    track = make_track("x")
    r1 = await svc.get(track)
    r2 = await svc.get(track)  # из кэша
    assert r1 == r2 == (SYNCED, "plain")
    client.find_both.assert_awaited_once()  # второй раз сеть не трогали


def test_set_synced_lrc_valid():
    svc = make_service()
    ok = svc.set_synced_lrc("vid", "[00:01.00] строка\n[00:05.00] вторая")
    assert ok is True
    # положилось в кэш как synced
    assert svc._cache["vid"][0].startswith("[00:01")


def test_set_synced_lrc_rejects_non_lrc():
    svc = make_service()
    assert svc.set_synced_lrc("vid", "просто текст без таймкодов") is False
    assert "vid" not in svc._cache


def test_prefetch_spawns_once():
    spawned = []
    client = SimpleNamespace(find_both=AsyncMock(return_value=(None, None)))
    svc = make_service(client=client, spawn=lambda coro: (spawned.append(coro), coro.close()))
    track = make_track("y")
    svc.prefetch(track)
    assert len(spawned) == 1
    # уже в pending — повторно не спавним
    svc._pending.add("z")
    svc.prefetch(make_track("z"))
    assert len(spawned) == 1


# --- эмбеды -----------------------------------------------------------------


def test_plain_embed():
    svc = make_service()
    embed = svc.plain_embed(make_track(title="Заголовок"), "текст песни")
    assert "Заголовок" in embed.title
    assert "текст песни" in embed.description


def test_karaoke_embed_before_start():
    svc = make_service()
    blocks = [(0.0, ["a", "b"]), (5.0, ["c"])]
    embed = svc._karaoke_embed(10, "T", blocks, index=-1, elapsed=0)
    assert "сейчас начнётся" in embed.description


def test_karaoke_embed_current_block_bold():
    svc = make_service()
    blocks = [(0.0, ["строка"]), (5.0, ["следующая"])]
    embed = svc._karaoke_embed(10, "T", blocks, index=0, elapsed=3)
    assert "**строка**" in embed.description
    assert "абзац 1/2" in embed.footer.text


def test_karaoke_embed_ansi_colored():
    svc = make_service()
    blocks = [(0.0, ["строка"]), (5.0, ["следующая"])]
    embed = svc._karaoke_embed(10, "T", blocks, index=0, elapsed=3, ansi=True)
    assert "```ansi" in embed.description  # цветной блок
    assert "\x1b[1;36m" in embed.description  # текущая строка — цветом
    assert "**строка**" not in embed.description  # не markdown-жирный


def test_ansi_enabled_reads_setting():
    # дефолт из Settings (True), и переопределение через провайдер
    from unittest.mock import MagicMock as MM

    svc = make_service()
    assert svc._ansi_enabled(10) is True  # gs=None -> дефолт
    svc._gs = MM()
    svc._gs.get = MM(return_value=0)  # /config выключил
    assert svc._ansi_enabled(10) is False


# --- стоп/старт караоке -----------------------------------------------------


def test_stop_karaoke_no_session():
    svc = make_service(session=None)
    assert svc.stop_karaoke(10) is False


def test_stop_karaoke_cancels_task():
    task = MagicMock()
    message = MagicMock()
    session = GuildMusicSession(player=MagicMock(), karaoke_task=task, karaoke_message=message)
    spawned = []
    svc = make_service(session=session, spawn=lambda c: (spawned.append(c), c.close()))
    assert svc.stop_karaoke(10) is True
    task.cancel.assert_called_once()
    assert session.karaoke_task is None


async def test_start_karaoke_no_synced_returns_false():
    player = MagicMock()
    session = GuildMusicSession(player=player)
    svc = make_service(session=session)
    channel = MagicMock()
    channel.send = AsyncMock()
    assert await svc.start_karaoke(channel, 10, make_track(), synced=None) is False


async def test_start_karaoke_sends_message(monkeypatch):
    monkeypatch.setattr(
        "src.infrastructure.discord.cogs.music.lyrics.asyncio.create_task",
        lambda coro: (coro.close(), MagicMock())[1],
    )
    player = MagicMock()
    player.elapsed_precise.return_value = 0.0
    player.elapsed.return_value = 0
    session = GuildMusicSession(player=player)
    svc = make_service(session=session)
    channel = MagicMock()
    message = MagicMock()
    channel.send = AsyncMock(return_value=message)
    ok = await svc.start_karaoke(channel, 10, make_track(), synced=SYNCED)
    assert ok is True
    channel.send.assert_awaited_once()
    assert session.karaoke_message is message


# --- toggle (кнопка 📜) -----------------------------------------------------


async def test_toggle_turns_off_when_running():
    task = MagicMock()
    session = GuildMusicSession(player=MagicMock(), karaoke_task=task)
    svc = make_service(session=session, spawn=lambda c: c.close())
    interaction = MagicMock()
    interaction.guild_id = 10
    interaction.response = MagicMock(send_message=AsyncMock())
    await svc.toggle(interaction)
    assert "выключено" in interaction.response.send_message.await_args.args[0]


async def test_toggle_nothing_playing():
    player = MagicMock()
    player.current = None
    session = GuildMusicSession(player=player)
    svc = make_service(session=session)
    interaction = MagicMock()
    interaction.guild_id = 10
    interaction.response = MagicMock(send_message=AsyncMock())
    await svc.toggle(interaction)
    assert "ничего не играет" in interaction.response.send_message.await_args.args[0]


async def test_toggle_falls_back_to_plain(monkeypatch):
    # synced нет, но есть plain -> показываем текстом
    client = SimpleNamespace(find_both=AsyncMock(return_value=(None, "плейн")))
    player = MagicMock()
    player.current = make_track()
    session = GuildMusicSession(player=player)
    svc = make_service(client=client, session=session)
    interaction = MagicMock()
    interaction.guild_id = 10
    interaction.channel = MagicMock()
    interaction.response = MagicMock(defer=AsyncMock(), send_message=AsyncMock())
    interaction.followup = MagicMock(send=AsyncMock())
    await svc.toggle(interaction)
    assert "embed" in interaction.followup.send.await_args.kwargs


async def test_toggle_no_lyrics_found():
    client = SimpleNamespace(find_both=AsyncMock(return_value=(None, None)))
    player = MagicMock()
    player.current = make_track()
    session = GuildMusicSession(player=player)
    svc = make_service(client=client, session=session)
    interaction = MagicMock()
    interaction.guild_id = 10
    interaction.channel = MagicMock()
    interaction.response = MagicMock(defer=AsyncMock(), send_message=AsyncMock())
    interaction.followup = MagicMock(send=AsyncMock())
    await svc.toggle(interaction)
    assert "не нашла" in interaction.followup.send.await_args.args[0]
