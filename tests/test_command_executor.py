"""DiscordCommandExecutor: единственное место, где команда панели превращается в
реальное Discord-действие (бан/мут/музыка/профиль/роли). Discord — лёгкие
фейки; сравнение ролей (`role >= guild.me.top_role`) реализовано с той же
семантикой, что и discord.py (@everyone ниже всех, дальше по position/id),
права/цвет — настоящие discord.Permissions/Colour, это плоские value-объекты,
фейковать их незачем."""

import base64
from datetime import UTC, datetime
from functools import total_ordering
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import discord
import pytest

from src.infrastructure.commands.bridge import Command, CommandError
from src.infrastructure.discord.command_executor import (
    DiscordCommandExecutor,
    _decode_data_url,
    _download_image,
)
from tests.cog_fakes import forbidden, http_error, not_found

GUILD = 10
BOT_ID = 999


class _ImageResponse:
    """Фейк aiohttp-ответа с .content.read() — обычный FakeResponse из
    aiohttp_fakes даёт только .json()/.text(), сюда не подходит."""

    def __init__(self, status=200, content_type="image/png", data=b"\x89PNG"):
        self.status = status
        self.headers = {"Content-Type": content_type} if content_type else {}
        self._data = data
        self.content = SimpleNamespace(read=AsyncMock(side_effect=lambda n: self._data[:n]))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _ImageSession:
    def __init__(self, response=None, get_error=None):
        self._response = response
        self._get_error = get_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, timeout=None):
        if self._get_error:
            raise self._get_error
        return self._response


def patch_image_session(monkeypatch, response=None, get_error=None):
    session = _ImageSession(response=response, get_error=get_error)
    monkeypatch.setattr(
        "src.infrastructure.discord.command_executor.aiohttp.ClientSession",
        lambda **kw: session,
    )
    return session


@pytest.fixture(autouse=True)
def _bypass_ssrf_guard(monkeypatch):
    # SSRF-щит _download_image резолвит хост в сеть; в юнит-тестах его глушим,
    # чтобы не зависеть от DNS. Сам щит проверяется в test_ssrf_guard.py.
    monkeypatch.setattr(
        "src.infrastructure.discord.command_executor._assert_public_url",
        AsyncMock(),
    )


@total_ordering
class FakeRole:
    def __init__(self, guild, role_id, name="Role", position=1, managed=False, permissions=0):
        self.guild = guild
        self.id = role_id
        self.name = name
        self.position = position
        self.managed = managed
        self.permissions = discord.Permissions(permissions)
        self.hoist = False
        self.mentionable = False
        self.color = discord.Colour.default()
        self.edit = AsyncMock()
        self.delete = AsyncMock()

    def is_default(self):
        return self.id == self.guild.id

    def __eq__(self, other):
        return isinstance(other, FakeRole) and self.id == other.id

    def __hash__(self):
        return hash(self.id)

    def __lt__(self, other):
        # семантика discord.py: @everyone ниже всех, дальше по position/id
        if self.is_default():
            return not other.is_default()
        if other.is_default():
            return False
        if self.position != other.position:
            return self.position < other.position
        return self.id > other.id


class FakeMember:
    def __init__(self, guild, user_id, roles=(), bot=False):
        self.guild = guild
        self.id = user_id
        self.roles = list(roles)
        self.bot = bot
        self.add_roles = AsyncMock()
        self.remove_roles = AsyncMock()
        self.timeout = AsyncMock()
        self.edit = AsyncMock()


class FakeGuild:
    def __init__(self, guild_id=GUILD, bot_top_position=10, bot_permissions=None):
        self.id = guild_id
        self._roles: dict[int, FakeRole] = {}
        self._members: dict[int, FakeMember] = {}
        everyone = FakeRole(self, guild_id, name="@everyone", position=0)
        self._roles[guild_id] = everyone
        bot_top = FakeRole(self, guild_id + 1, name="BotTop", position=bot_top_position)
        me = FakeMember(self, BOT_ID, roles=[everyone, bot_top])
        me.top_role = bot_top
        me.guild_permissions = (
            discord.Permissions.all() if bot_permissions is None else bot_permissions
        )
        me.edit = AsyncMock()
        self.me = me
        self.ban = AsyncMock()
        self.unban = AsyncMock()
        self.create_role = AsyncMock(return_value=FakeRole(self, 555, name="Created"))
        self.edit_role_positions = AsyncMock()
        self.fetch_member = AsyncMock(side_effect=not_found())

    @property
    def roles(self):
        return list(self._roles.values())

    @property
    def members(self):
        return list(self._members.values())

    def add_role(self, role):
        self._roles[role.id] = role
        return role

    def add_member(self, member):
        self._members[member.id] = member
        return member

    def get_role(self, role_id):
        return self._roles.get(role_id)

    def get_member(self, user_id):
        return self._members.get(user_id)


def add_role(guild, role_id, position=1, managed=False, permissions=0, name="Role"):
    role = FakeRole(
        guild, role_id, name=name, position=position, managed=managed, permissions=permissions
    )
    guild.add_role(role)
    return role


def add_member(guild, user_id, roles=(), bot=False):
    member = FakeMember(guild, user_id, roles=roles, bot=bot)
    guild.add_member(member)
    return member


def make_moderation():
    moderation = MagicMock()
    moderation.temp_ban.execute = AsyncMock(return_value=datetime(2026, 7, 22, 12, 0, tzinfo=UTC))
    moderation.remove_ban.execute = AsyncMock()
    return moderation


def make_executor(guild=None, music_cog=None, moderation=None):
    bot = MagicMock()
    bot.get_guild = MagicMock(return_value=guild)
    bot.get_cog = MagicMock(return_value=music_cog)
    moderation = moderation or make_moderation()
    return DiscordCommandExecutor(bot, moderation), bot, moderation


def cmd(command_type, payload=None, guild_id=GUILD, requested_by=1, cmd_id=1):
    return Command(
        id=cmd_id,
        guild_id=guild_id,
        command_type=command_type,
        payload=payload or {},
        requested_by=requested_by,
    )


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # _role_bulk делает asyncio.sleep(0.2) между участниками — тестам это не нужно
    import src.infrastructure.discord.command_executor as mod

    monkeypatch.setattr(mod.asyncio, "sleep", AsyncMock())


# --- диспетчер ---


async def test_execute_guild_not_found():
    executor, _bot, _mod = make_executor(guild=None)
    with pytest.raises(CommandError, match="Бот не на этом сервере"):
        await executor.execute(cmd("mod.unban", {"user_id": "1"}))


async def test_execute_unknown_command_type():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="Неизвестная команда"):
        await executor.execute(cmd("nope.nope"))


# --- модерация ---


async def test_tempban_happy_path():
    guild = FakeGuild()
    executor, _bot, moderation = make_executor(guild=guild)

    result = await executor.execute(
        cmd("mod.tempban", {"user_id": "42", "minutes": 60, "reason": "спам"}, requested_by=7)
    )

    guild.ban.assert_awaited_once()
    ban_args = guild.ban.await_args
    assert ban_args.args[0].id == 42
    moderation.temp_ban.execute.assert_awaited_once()
    m_args = moderation.temp_ban.execute.await_args.args
    assert m_args[:5] == (42, GUILD, 7, "спам", 60)
    assert isinstance(m_args[5], datetime)
    assert result == "Забанен до 22.07.2026 12:00 UTC."


async def test_tempban_default_reason():
    guild = FakeGuild()
    executor, _bot, moderation = make_executor(guild=guild)
    await executor.execute(cmd("mod.tempban", {"user_id": "1", "minutes": 5}))
    assert moderation.temp_ban.execute.await_args.args[3] == "без причины"


async def test_tempban_forbidden():
    guild = FakeGuild()
    guild.ban = AsyncMock(side_effect=forbidden())
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="Ban Members"):
        await executor.execute(cmd("mod.tempban", {"user_id": "1", "minutes": 5}))


async def test_unban_happy_path():
    guild = FakeGuild()
    executor, _bot, moderation = make_executor(guild=guild)
    result = await executor.execute(cmd("mod.unban", {"user_id": "5"}))
    guild.unban.assert_awaited_once()
    assert guild.unban.await_args.args[0].id == 5
    moderation.remove_ban.execute.assert_awaited_once_with(5, GUILD)
    assert result == "Разбанен."


async def test_unban_not_found():
    guild = FakeGuild()
    guild.unban = AsyncMock(side_effect=not_found())
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="не в бане"):
        await executor.execute(cmd("mod.unban", {"user_id": "5"}))


async def test_unban_forbidden():
    guild = FakeGuild()
    guild.unban = AsyncMock(side_effect=forbidden())
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="Ban Members"):
        await executor.execute(cmd("mod.unban", {"user_id": "5"}))


async def test_mute_found_in_cache():
    guild = FakeGuild()
    member = add_member(guild, 3)
    executor, _bot, _mod = make_executor(guild=guild)
    result = await executor.execute(cmd("mod.mute", {"user_id": "3", "minutes": 30}))
    member.timeout.assert_awaited_once()
    assert result == "Замучен на 30 мин."


async def test_mute_falls_back_to_fetch_member():
    guild = FakeGuild()
    fetched = FakeMember(guild, 4)
    guild.fetch_member = AsyncMock(return_value=fetched)
    executor, _bot, _mod = make_executor(guild=guild)
    await executor.execute(cmd("mod.mute", {"user_id": "4", "minutes": 30}))
    fetched.timeout.assert_awaited_once()


async def test_mute_member_not_found_anywhere():
    guild = FakeGuild()  # fetch_member по умолчанию кидает not_found()
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="Участник не найден"):
        await executor.execute(cmd("mod.mute", {"user_id": "4", "minutes": 30}))


async def test_mute_forbidden():
    guild = FakeGuild()
    member = add_member(guild, 3)
    member.timeout = AsyncMock(side_effect=forbidden())
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="Timeout Members"):
        await executor.execute(cmd("mod.mute", {"user_id": "3", "minutes": 30}))


async def test_unmute_happy_path():
    guild = FakeGuild()
    member = add_member(guild, 3)
    executor, _bot, _mod = make_executor(guild=guild)
    result = await executor.execute(cmd("mod.unmute", {"user_id": "3"}))
    member.timeout.assert_awaited_once_with(None, reason="Снято из панели")
    assert result == "Мут снят."


async def test_unmute_forbidden():
    guild = FakeGuild()
    member = add_member(guild, 3)
    member.timeout = AsyncMock(side_effect=forbidden())
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="Timeout Members"):
        await executor.execute(cmd("mod.unmute", {"user_id": "3"}))


# --- музыка ---


def make_music_cog(player=None):
    service = MagicMock()
    service.get_player = MagicMock(return_value=player)
    service.cleanup = AsyncMock()
    return SimpleNamespace(service=service), service


def make_player(is_paused=False):
    player = MagicMock()
    player.is_paused = is_paused
    player.toggle_pause = AsyncMock()
    player.skip = AsyncMock()
    return player


async def test_music_no_cog():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild, music_cog=None)
    with pytest.raises(CommandError, match="недоступен"):
        await executor.execute(cmd("music.pause"))


async def test_music_no_player():
    guild = FakeGuild()
    cog, _service = make_music_cog(player=None)
    executor, _bot, _mod = make_executor(guild=guild, music_cog=cog)
    with pytest.raises(CommandError, match="ничего не играет"):
        await executor.execute(cmd("music.pause"))


async def test_pause_toggles_when_playing():
    guild = FakeGuild()
    player = make_player(is_paused=False)
    cog, _service = make_music_cog(player)
    executor, _bot, _mod = make_executor(guild=guild, music_cog=cog)
    result = await executor.execute(cmd("music.pause"))
    player.toggle_pause.assert_awaited_once()
    assert result == "Пауза."


async def test_pause_noop_when_already_paused():
    guild = FakeGuild()
    player = make_player(is_paused=True)
    cog, _service = make_music_cog(player)
    executor, _bot, _mod = make_executor(guild=guild, music_cog=cog)
    result = await executor.execute(cmd("music.pause"))
    player.toggle_pause.assert_not_awaited()
    assert result == "Уже на паузе."


async def test_resume_toggles_when_paused():
    guild = FakeGuild()
    player = make_player(is_paused=True)
    cog, _service = make_music_cog(player)
    executor, _bot, _mod = make_executor(guild=guild, music_cog=cog)
    result = await executor.execute(cmd("music.resume"))
    player.toggle_pause.assert_awaited_once()
    assert result == "Продолжаю."


async def test_resume_noop_when_already_playing():
    guild = FakeGuild()
    player = make_player(is_paused=False)
    cog, _service = make_music_cog(player)
    executor, _bot, _mod = make_executor(guild=guild, music_cog=cog)
    result = await executor.execute(cmd("music.resume"))
    player.toggle_pause.assert_not_awaited()
    assert result == "Уже играет."


async def test_skip():
    guild = FakeGuild()
    player = make_player()
    cog, _service = make_music_cog(player)
    executor, _bot, _mod = make_executor(guild=guild, music_cog=cog)
    result = await executor.execute(cmd("music.skip"))
    player.skip.assert_awaited_once()
    assert result == "Пропущено."


async def test_stop_cleans_up_service():
    guild = FakeGuild()
    player = make_player()
    cog, service = make_music_cog(player)
    executor, _bot, _mod = make_executor(guild=guild, music_cog=cog)
    result = await executor.execute(cmd("music.stop"))
    service.cleanup.assert_awaited_once_with(GUILD, "⏹️ Остановлено из панели.")
    assert result == "Остановлено."


# --- профиль бота ---


async def test_profile_apply_nick_only():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    result = await executor.execute(cmd("profile.apply", {"nick": "Новый Ник"}))
    guild.me.edit.assert_awaited_once_with(nick="Новый Ник")
    assert result == "Профиль обновлён: nick."


async def test_profile_apply_blank_nick_resets():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    await executor.execute(cmd("profile.apply", {"nick": "   "}))
    guild.me.edit.assert_awaited_once_with(nick=None)


async def test_profile_apply_nothing_to_change():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    result = await executor.execute(cmd("profile.apply", {}))
    guild.me.edit.assert_not_awaited()
    assert result == "Нечего менять."


async def test_profile_apply_avatar_data_wins_over_url():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    raw = b"\x89PNG-bytes"
    data_url = "data:image/png;base64," + base64.b64encode(raw).decode()
    await executor.execute(
        cmd("profile.apply", {"avatar_data": data_url, "avatar_url": "http://example.com/x.png"})
    )
    assert guild.me.edit.await_args.kwargs["avatar"] == raw


async def test_profile_apply_avatar_url_downloads(monkeypatch):
    import src.infrastructure.discord.command_executor as mod

    download = AsyncMock(return_value=b"downloaded-bytes")
    monkeypatch.setattr(mod, "_download_image", download)
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    await executor.execute(cmd("profile.apply", {"avatar_url": "http://example.com/x.png"}))
    download.assert_awaited_once_with("http://example.com/x.png")
    assert guild.me.edit.await_args.kwargs["avatar"] == b"downloaded-bytes"


async def test_profile_apply_empty_avatar_url_resets():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    await executor.execute(cmd("profile.apply", {"avatar_url": ""}))
    assert guild.me.edit.await_args.kwargs["avatar"] is None


async def test_profile_apply_banner_url_downloads(monkeypatch):
    patch_image_session(monkeypatch, response=_ImageResponse(data=b"banner-from-url"))
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    await executor.execute(cmd("profile.apply", {"banner_url": "http://example.com/b.png"}))
    assert guild.me.edit.await_args.kwargs["banner"] == b"banner-from-url"


async def test_download_image_rejects_bad_scheme():
    with pytest.raises(CommandError, match="http"):
        await _download_image("ftp://example.com/x.png")


async def test_download_image_happy_path(monkeypatch):
    patch_image_session(monkeypatch, response=_ImageResponse(data=b"real-bytes"))
    assert await _download_image("http://example.com/x.png") == b"real-bytes"


async def test_download_image_non_200(monkeypatch):
    patch_image_session(monkeypatch, response=_ImageResponse(status=404))
    with pytest.raises(CommandError, match="HTTP 404"):
        await _download_image("http://example.com/x.png")


async def test_download_image_wrong_content_type(monkeypatch):
    patch_image_session(monkeypatch, response=_ImageResponse(content_type="text/html"))
    with pytest.raises(CommandError, match="не картинка"):
        await _download_image("http://example.com/x.png")


async def test_download_image_too_large(monkeypatch):
    patch_image_session(monkeypatch, response=_ImageResponse(data=b"a" * (8 * 1024 * 1024 + 1)))
    with pytest.raises(CommandError, match="больше 8"):
        await _download_image("http://example.com/x.png")


async def test_download_image_client_error(monkeypatch):
    patch_image_session(monkeypatch, get_error=aiohttp.ClientError("conn refused"))
    with pytest.raises(CommandError, match="Ошибка загрузки"):
        await _download_image("http://example.com/x.png")


async def test_profile_apply_banner_data(monkeypatch):
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    raw = b"banner-bytes"
    data_url = "data:image/png;base64," + base64.b64encode(raw).decode()
    result = await executor.execute(cmd("profile.apply", {"banner_data": data_url}))
    assert guild.me.edit.await_args.kwargs["banner"] == raw
    assert result == "Профиль обновлён: banner."


async def test_profile_apply_forbidden():
    guild = FakeGuild()
    guild.me.edit = AsyncMock(side_effect=forbidden())
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="Change Nickname"):
        await executor.execute(cmd("profile.apply", {"nick": "X"}))


async def test_profile_apply_http_exception_uses_text():
    guild = FakeGuild()
    guild.me.edit = AsyncMock(side_effect=http_error())
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="boom"):
        await executor.execute(cmd("profile.apply", {"nick": "X"}))


def test_decode_data_url_invalid_base64():
    with pytest.raises(CommandError, match="Битые данные"):
        _decode_data_url("data:image/png;base64,не-base64!!!")


def test_decode_data_url_too_large():
    raw = b"a" * (8 * 1024 * 1024 + 1)
    data_url = "data:image/png;base64," + base64.b64encode(raw).decode()
    with pytest.raises(CommandError, match="больше 8"):
        _decode_data_url(data_url)


def test_decode_data_url_without_prefix():
    raw = b"just bytes"
    encoded = base64.b64encode(raw).decode()
    assert _decode_data_url(encoded) == raw


# --- роли: выдача/снятие участнику ---


async def test_role_assign_happy_path():
    guild = FakeGuild()
    role = add_role(guild, 1, position=2)
    member = add_member(guild, 5)
    executor, _bot, _mod = make_executor(guild=guild)
    result = await executor.execute(cmd("role.assign", {"role_id": "1", "user_id": "5"}))
    member.add_roles.assert_awaited_once_with(role, reason="Панель: выдача роли")
    assert "Выдал роль" in result


async def test_role_assign_already_has_role():
    guild = FakeGuild()
    role = add_role(guild, 1, position=2)
    member = add_member(guild, 5, roles=[role])
    executor, _bot, _mod = make_executor(guild=guild)
    result = await executor.execute(cmd("role.assign", {"role_id": "1", "user_id": "5"}))
    member.add_roles.assert_not_awaited()
    assert "уже есть" in result


async def test_role_assign_forbidden():
    guild = FakeGuild()
    add_role(guild, 1, position=2)
    member = add_member(guild, 5)
    member.add_roles = AsyncMock(side_effect=forbidden())
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="Manage Roles"):
        await executor.execute(cmd("role.assign", {"role_id": "1", "user_id": "5"}))


async def test_role_unassign_happy_path():
    guild = FakeGuild()
    role = add_role(guild, 1, position=2)
    member = add_member(guild, 5, roles=[role])
    executor, _bot, _mod = make_executor(guild=guild)
    result = await executor.execute(cmd("role.unassign", {"role_id": "1", "user_id": "5"}))
    member.remove_roles.assert_awaited_once_with(role, reason="Панель: снятие роли")
    assert "Снял роль" in result


async def test_role_unassign_doesnt_have_role():
    guild = FakeGuild()
    add_role(guild, 1, position=2)
    member = add_member(guild, 5)
    executor, _bot, _mod = make_executor(guild=guild)
    result = await executor.execute(cmd("role.unassign", {"role_id": "1", "user_id": "5"}))
    member.remove_roles.assert_not_awaited()
    assert "и так нет" in result


async def test_role_unassign_forbidden():
    guild = FakeGuild()
    role = add_role(guild, 1, position=2)
    member = add_member(guild, 5, roles=[role])
    member.remove_roles = AsyncMock(side_effect=forbidden())
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="Manage Roles"):
        await executor.execute(cmd("role.unassign", {"role_id": "1", "user_id": "5"}))


# --- _manageable_role: ограждения ---


async def test_manageable_role_not_found():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="не найдена"):
        await executor.execute(cmd("role.delete", {"role_id": "999"}))


async def test_manageable_role_rejects_everyone():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="@everyone"):
        await executor.execute(cmd("role.delete", {"role_id": str(GUILD)}))


async def test_manageable_role_rejects_managed():
    guild = FakeGuild()
    add_role(guild, 1, position=2, managed=True)
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="интеграции"):
        await executor.execute(cmd("role.delete", {"role_id": "1"}))


async def test_manageable_role_rejects_above_bot():
    guild = FakeGuild(bot_top_position=10)
    add_role(guild, 1, position=99)  # выше бота
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="выше моей"):
        await executor.execute(cmd("role.delete", {"role_id": "1"}))


# --- роли: CRUD ---


async def test_role_create_happy_path():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    result = await executor.execute(
        cmd("role.create", {"name": "Новая", "color": 255, "hoist": True, "mentionable": False})
    )
    guild.create_role.assert_awaited_once()
    kwargs = guild.create_role.await_args.kwargs
    assert kwargs["name"] == "Новая"
    assert kwargs["colour"].value == 255
    assert kwargs["hoist"] is True
    assert "Создал роль" in result


async def test_role_create_empty_name_rejected():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="пустым"):
        await executor.execute(cmd("role.create", {"name": "   "}))


async def test_role_create_name_too_long_rejected():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="100 символов"):
        await executor.execute(cmd("role.create", {"name": "x" * 101}))


async def test_role_create_bad_color_type_rejected():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="числом"):
        await executor.execute(cmd("role.create", {"name": "X", "color": "not-a-number"}))


async def test_role_create_color_out_of_range_rejected():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="диапазона"):
        await executor.execute(cmd("role.create", {"name": "X", "color": 0x1000000}))


async def test_role_create_empty_color_is_default():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    await executor.execute(cmd("role.create", {"name": "X", "color": ""}))
    assert guild.create_role.await_args.kwargs["colour"] == discord.Colour.default()


async def test_role_create_forbidden():
    guild = FakeGuild()
    guild.create_role = AsyncMock(side_effect=forbidden())
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="Manage Roles"):
        await executor.execute(cmd("role.create", {"name": "X"}))


async def test_role_edit_partial_fields():
    guild = FakeGuild()
    role = add_role(guild, 1, position=2)
    executor, _bot, _mod = make_executor(guild=guild)
    result = await executor.execute(cmd("role.edit", {"role_id": "1", "hoist": True}))
    role.edit.assert_awaited_once_with(reason="Панель: изменение роли", hoist=True)
    assert "Обновил роль" in result


async def test_role_edit_color_and_mentionable():
    guild = FakeGuild()
    role = add_role(guild, 1, position=2)
    executor, _bot, _mod = make_executor(guild=guild)
    await executor.execute(cmd("role.edit", {"role_id": "1", "color": 128, "mentionable": True}))
    kwargs = role.edit.await_args.kwargs
    assert kwargs["colour"].value == 128
    assert kwargs["mentionable"] is True


async def test_role_edit_nothing_to_change():
    guild = FakeGuild()
    role = add_role(guild, 1, position=2)
    executor, _bot, _mod = make_executor(guild=guild)
    result = await executor.execute(cmd("role.edit", {"role_id": "1"}))
    role.edit.assert_not_awaited()
    assert result == "Нечего менять."


async def test_role_edit_forbidden():
    guild = FakeGuild()
    role = add_role(guild, 1, position=2)
    role.edit = AsyncMock(side_effect=forbidden())
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="выше моей"):
        await executor.execute(cmd("role.edit", {"role_id": "1", "name": "X"}))


async def test_role_delete_happy_path():
    guild = FakeGuild()
    role = add_role(guild, 1, position=2, name="Уходит")
    executor, _bot, _mod = make_executor(guild=guild)
    result = await executor.execute(cmd("role.delete", {"role_id": "1"}))
    role.delete.assert_awaited_once_with(reason="Панель: удаление роли")
    assert result == "Удалил роль «Уходит»."


async def test_role_delete_forbidden():
    guild = FakeGuild()
    role = add_role(guild, 1, position=2)
    role.delete = AsyncMock(side_effect=forbidden())
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="Manage Roles"):
        await executor.execute(cmd("role.delete", {"role_id": "1"}))


# --- роли: порядок ---


async def test_role_reorder_happy_path():
    guild = FakeGuild(bot_top_position=10)
    role_a = add_role(guild, 1, position=2)
    role_b = add_role(guild, 2, position=4)
    role_c = add_role(guild, 3, position=6)
    executor, _bot, _mod = make_executor(guild=guild)

    result = await executor.execute(cmd("role.reorder", {"order": ["3", "1", "2"]}))

    guild.edit_role_positions.assert_awaited_once()
    positions = guild.edit_role_positions.await_args.kwargs["positions"]
    assert positions == {role_b: 2, role_a: 4, role_c: 6}
    assert result == "Порядок ролей обновлён."


async def test_role_reorder_stale_list_rejected():
    guild = FakeGuild(bot_top_position=10)
    add_role(guild, 1, position=2)
    add_role(guild, 2, position=4)  # не включена в order -> список устарел
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="устарел"):
        await executor.execute(cmd("role.reorder", {"order": ["1"]}))


async def test_role_reorder_single_role_noop():
    guild = FakeGuild(bot_top_position=10)
    add_role(guild, 1, position=2)
    executor, _bot, _mod = make_executor(guild=guild)
    result = await executor.execute(cmd("role.reorder", {"order": ["1"]}))
    guild.edit_role_positions.assert_not_awaited()
    assert result == "Нечего переставлять."


async def test_role_reorder_forbidden():
    guild = FakeGuild(bot_top_position=10)
    add_role(guild, 1, position=2)
    add_role(guild, 2, position=4)
    guild.edit_role_positions = AsyncMock(side_effect=forbidden())
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="Manage Roles"):
        await executor.execute(cmd("role.reorder", {"order": ["1", "2"]}))


# --- роли: права ---


async def test_role_permissions_administrator_never_granted():
    guild = FakeGuild(bot_permissions=discord.Permissions(manage_roles=True, administrator=True))
    role = add_role(guild, 1, position=2, permissions=discord.Permissions(send_messages=True).value)
    executor, _bot, _mod = make_executor(guild=guild)

    requested = discord.Permissions(administrator=True, manage_roles=True).value
    result = await executor.execute(
        cmd("role.permissions", {"role_id": "1", "permissions": str(requested)})
    )

    applied = role.edit.await_args.kwargs["permissions"]
    assert applied.administrator is False  # панель admin никогда не выдаёт
    assert applied.manage_roles is True  # бот этим правом владеет -> применили запрошенное
    assert "Обновил права" in result


async def test_role_permissions_bot_lacks_flag_keeps_current():
    # бот не имеет ban_members -> запрошенное значение этого флага игнорируется
    guild = FakeGuild(bot_permissions=discord.Permissions(manage_roles=True))
    current = discord.Permissions(manage_roles=False, ban_members=False)
    role = add_role(guild, 1, position=2, permissions=current.value)
    executor, _bot, _mod = make_executor(guild=guild)

    requested = discord.Permissions(manage_roles=True, ban_members=True).value
    await executor.execute(cmd("role.permissions", {"role_id": "1", "permissions": str(requested)}))

    applied = role.edit.await_args.kwargs["permissions"]
    assert applied.manage_roles is True  # бот владеет -> применили
    assert applied.ban_members is False  # бот не владеет -> оставили как было


async def test_role_permissions_no_change_skips_edit():
    guild = FakeGuild(bot_permissions=discord.Permissions(manage_roles=True))
    current = discord.Permissions(manage_roles=True)
    role = add_role(guild, 1, position=2, permissions=current.value)
    executor, _bot, _mod = make_executor(guild=guild)

    result = await executor.execute(
        cmd("role.permissions", {"role_id": "1", "permissions": str(current.value)})
    )

    role.edit.assert_not_awaited()
    assert result == "Права не изменились."


async def test_role_permissions_forbidden():
    guild = FakeGuild(bot_permissions=discord.Permissions(manage_roles=True))
    role = add_role(guild, 1, position=2, permissions=0)
    role.edit = AsyncMock(side_effect=forbidden())
    executor, _bot, _mod = make_executor(guild=guild)
    requested = discord.Permissions(manage_roles=True).value
    with pytest.raises(CommandError, match="Manage Roles"):
        await executor.execute(
            cmd("role.permissions", {"role_id": "1", "permissions": str(requested)})
        )


# --- роли: массовая выдача/снятие ---


async def test_role_bulk_assign_skips_bots_and_holders():
    guild = FakeGuild()
    role = add_role(guild, 1, position=2)
    holder = add_member(guild, 1, roles=[role])
    a_bot = add_member(guild, 2, bot=True)
    target = add_member(guild, 3)
    executor, _bot, _mod = make_executor(guild=guild)

    result = await executor.execute(cmd("role.bulk", {"role_id": "1", "op": "assign"}))

    holder.add_roles.assert_not_awaited()
    a_bot.add_roles.assert_not_awaited()
    target.add_roles.assert_awaited_once_with(role, reason="Панель: массовая выдача роли")
    assert "Выдал роль" in result and "1 чел" in result


async def test_role_bulk_unassign_includes_bots():
    guild = FakeGuild()
    role = add_role(guild, 1, position=2)
    bot_holder = add_member(guild, 2, roles=[role], bot=True)
    non_holder = add_member(guild, 3)
    executor, _bot, _mod = make_executor(guild=guild)

    result = await executor.execute(cmd("role.bulk", {"role_id": "1", "op": "unassign"}))

    bot_holder.remove_roles.assert_awaited_once_with(role, reason="Панель: массовое снятие роли")
    non_holder.remove_roles.assert_not_awaited()
    assert "Снял роль" in result


async def test_role_bulk_empty_targets():
    guild = FakeGuild()
    add_role(guild, 1, position=2)
    executor, _bot, _mod = make_executor(guild=guild)
    result = await executor.execute(cmd("role.bulk", {"role_id": "1", "op": "assign"}))
    assert result == "Некого менять — список пуст."


async def test_role_bulk_unknown_op():
    guild = FakeGuild()
    add_role(guild, 1, position=2)
    add_member(guild, 3)
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="Неизвестная операция"):
        await executor.execute(cmd("role.bulk", {"role_id": "1", "op": "frobnicate"}))


async def test_role_bulk_over_cap_rejected():
    guild = FakeGuild()
    add_role(guild, 1, position=2)
    for uid in range(201):
        add_member(guild, uid + 10)
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="предел безопасности"):
        await executor.execute(cmd("role.bulk", {"role_id": "1", "op": "assign"}))


async def test_role_bulk_stops_immediately_on_forbidden():
    guild = FakeGuild()
    add_role(guild, 1, position=2)
    first = add_member(guild, 3)
    second = add_member(guild, 4)
    first.add_roles = AsyncMock(side_effect=forbidden())
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="Manage Roles"):
        await executor.execute(cmd("role.bulk", {"role_id": "1", "op": "assign"}))
    second.add_roles.assert_not_awaited()  # до второго не дошли


async def test_role_bulk_counts_partial_http_failures():
    guild = FakeGuild()
    add_role(guild, 1, position=2)
    ok_member = add_member(guild, 3)
    failing_member = add_member(guild, 4)
    failing_member.add_roles = AsyncMock(side_effect=http_error())
    executor, _bot, _mod = make_executor(guild=guild)

    result = await executor.execute(cmd("role.bulk", {"role_id": "1", "op": "assign"}))

    ok_member.add_roles.assert_awaited_once()
    assert "1 чел" in result and "ошибок 1" in result  # "N чел" считает только успехи


# --- role.import (шаблоны/экспорт) ---


async def test_role_import_creates_missing_roles():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    payload = {
        "roles": [
            {"name": "Alpha", "color": 255, "hoist": True, "mentionable": False},
            {"name": "Beta"},
        ]
    }
    result = await executor.execute(cmd("role.import", payload))
    assert guild.create_role.await_count == 2
    first = guild.create_role.await_args_list[0].kwargs
    assert first["name"] == "Alpha"
    assert first["colour"] == discord.Colour(255)
    assert first["hoist"] is True
    assert result == "Создано ролей: 2."


async def test_role_import_skips_existing_by_name_case_insensitive():
    guild = FakeGuild()
    add_role(guild, 1, position=2, name="Existing")
    executor, _bot, _mod = make_executor(guild=guild)
    payload = {"roles": [{"name": "existing"}, {"name": "Fresh"}]}
    result = await executor.execute(cmd("role.import", payload))
    assert guild.create_role.await_count == 1  # только Fresh
    assert "Создано ролей: 1" in result
    assert "Пропущено" in result


async def test_role_import_dedups_within_batch():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    payload = {"roles": [{"name": "Dup"}, {"name": "dup"}]}
    result = await executor.execute(cmd("role.import", payload))
    assert guild.create_role.await_count == 1
    assert "Пропущено" in result


async def test_role_import_skips_bad_entries():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    payload = {"roles": [{"name": ""}, "notadict", {"name": "Good"}]}
    result = await executor.execute(cmd("role.import", payload))
    assert guild.create_role.await_count == 1
    assert "Создано ролей: 1" in result


async def test_role_import_not_a_list_rejected():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="Некорректный формат"):
        await executor.execute(cmd("role.import", {"roles": "nope"}))


async def test_role_import_over_cap_rejected():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    payload = {"roles": [{"name": f"R{i}"} for i in range(51)]}
    with pytest.raises(CommandError, match="предел безопасности"):
        await executor.execute(cmd("role.import", payload))


async def test_role_import_nothing_created_when_all_skipped():
    guild = FakeGuild()
    executor, _bot, _mod = make_executor(guild=guild)
    result = await executor.execute(cmd("role.import", {"roles": [{"name": "@everyone"}]}))
    guild.create_role.assert_not_awaited()
    assert "Ничего не создано" in result


async def test_role_import_forbidden():
    guild = FakeGuild()
    guild.create_role = AsyncMock(side_effect=forbidden())
    executor, _bot, _mod = make_executor(guild=guild)
    with pytest.raises(CommandError, match="Manage Roles"):
        await executor.execute(cmd("role.import", {"roles": [{"name": "X"}]}))
