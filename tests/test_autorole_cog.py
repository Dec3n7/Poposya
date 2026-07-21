"""AutoRoleCog: выдача ролей новичку при входе.

Discord ненастоящий — проверяем контракт: какие роли ког решает выдать (только
доступные боту, не managed/@everyone, ниже его высшей), что ботам не выдаёт и
что провал прав/HTTP не роняет обработчик."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.infrastructure.discord.cogs.autorole import AutoRoleCog
from tests.cog_fakes import forbidden, http_error

GUILD = 10
BOT_TOP = 5


def _role(role_id, position=1, managed=False, default=False):
    return SimpleNamespace(
        id=role_id,
        position=position,
        managed=managed,
        is_default=lambda d=default: d,
    )


def _guild(roles, bot_top=BOT_TOP, has_me=True):
    role_map = {r.id: r for r in roles}
    me = SimpleNamespace(top_role=SimpleNamespace(position=bot_top)) if has_me else None
    return SimpleNamespace(id=GUILD, get_role=role_map.get, me=me)


def _member(guild, bot=False):
    return SimpleNamespace(id=1, guild=guild, bot=bot, add_roles=AsyncMock())


def make_cog(autorole_ids):
    settings = MagicMock()
    settings.get = MagicMock(return_value=autorole_ids)
    return AutoRoleCog(MagicMock(), settings), settings


async def test_assigns_configured_valid_roles():
    r1, r2 = _role(1, position=1), _role(2, position=2)
    guild = _guild([r1, r2])
    member = _member(guild)
    cog, settings = make_cog([1, 2])

    await cog.on_member_join(member)

    settings.get.assert_called_once_with(GUILD, "autorole_ids", [])
    member.add_roles.assert_awaited_once()
    assert {r.id for r in member.add_roles.await_args.args} == {1, 2}
    assert member.add_roles.await_args.kwargs["reason"] == "Автороль при входе"


async def test_empty_config_does_nothing():
    guild = _guild([_role(1)])
    member = _member(guild)
    cog, _settings = make_cog([])
    await cog.on_member_join(member)
    member.add_roles.assert_not_awaited()


async def test_bots_get_no_autorole():
    guild = _guild([_role(1)])
    member = _member(guild, bot=True)
    cog, settings = make_cog([1])
    await cog.on_member_join(member)
    member.add_roles.assert_not_awaited()
    settings.get.assert_not_called()  # выходим до чтения настроек


async def test_filters_invalid_roles():
    ok = _role(1, position=1)
    managed = _role(2, position=1, managed=True)
    above = _role(3, position=BOT_TOP + 1)  # выше высшей роли бота
    everyone = _role(4, position=0, default=True)
    guild = _guild([ok, managed, above, everyone])
    member = _member(guild)
    cog, _settings = make_cog([1, 2, 3, 4, 99])  # 99 — несуществующая

    await cog.on_member_join(member)

    member.add_roles.assert_awaited_once()
    assert {r.id for r in member.add_roles.await_args.args} == {1}


async def test_all_invalid_no_call():
    above = _role(3, position=BOT_TOP + 1)
    guild = _guild([above])
    member = _member(guild)
    cog, _settings = make_cog([3])
    await cog.on_member_join(member)
    member.add_roles.assert_not_awaited()


async def test_no_me_skips():
    guild = _guild([_role(1)], has_me=False)
    member = _member(guild)
    cog, _settings = make_cog([1])
    await cog.on_member_join(member)
    member.add_roles.assert_not_awaited()


async def test_forbidden_is_swallowed():
    guild = _guild([_role(1)])
    member = _member(guild)
    member.add_roles = AsyncMock(side_effect=forbidden())
    cog, _settings = make_cog([1])
    await cog.on_member_join(member)  # не должно бросить
    member.add_roles.assert_awaited_once()


async def test_http_error_is_swallowed():
    guild = _guild([_role(1)])
    member = _member(guild)
    member.add_roles = AsyncMock(side_effect=http_error())
    cog, _settings = make_cog([1])
    await cog.on_member_join(member)  # не должно бросить
    member.add_roles.assert_awaited_once()
