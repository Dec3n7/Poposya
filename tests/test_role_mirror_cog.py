"""RoleMirrorCog: держит зеркало ролей Discord в БД по gateway-событиям.

Discord тут не настоящий (нужен живой шлюз) — только контракт: какие use case'ы
контейнера зовутся, с какими аргументами и в каком составе (без @everyone)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.domain.roles.entities import GuildRole
from src.infrastructure.discord.cogs.role_mirror import RoleMirrorCog

GUILD = 10
EVERYONE = SimpleNamespace(id=GUILD, is_default=lambda: True)


def _role(role_id, name="Role", position=1, managed=False, default=False, color=0):
    role = MagicMock()
    role.id = role_id
    role.name = name
    role.position = position
    role.managed = managed
    role.hoist = False
    role.mentionable = False
    role.color = SimpleNamespace(value=color)
    role.permissions = SimpleNamespace(value=0)
    role.is_default.return_value = default
    return role


def _member(user_id, roles):
    return SimpleNamespace(id=user_id, roles=roles)


def _guild(guild_id=GUILD, roles=None, members=None, bot_id=999, bot_top_position=5):
    me = SimpleNamespace(id=bot_id, top_role=SimpleNamespace(position=bot_top_position))
    return SimpleNamespace(id=guild_id, roles=roles or [], members=members or [], me=me)


def make_cog(bot_guilds=None):
    container = MagicMock()
    container.sync_guild.execute = AsyncMock()
    container.upsert_role.execute = AsyncMock()
    container.delete_role.execute = AsyncMock()
    container.sync_members.execute = AsyncMock()
    container.set_member_roles.execute = AsyncMock()
    container.remove_member.execute = AsyncMock()
    bot = MagicMock()
    bot.guilds = bot_guilds or []
    cog = RoleMirrorCog(bot, container)
    return cog, container


async def test_on_ready_backfills_all_guilds():
    everyone = _role(GUILD, default=True)
    admin_role = _role(1, name="Admin", position=3)
    member = _member(1, [everyone, admin_role])
    guild = _guild(roles=[everyone, admin_role], members=[member])
    cog, container = make_cog(bot_guilds=[guild])

    await cog.on_ready()

    container.sync_guild.execute.assert_awaited_once()
    args = container.sync_guild.execute.await_args.args
    assert args[0] == GUILD
    assert {r.role_id for r in args[1]} == {GUILD, 1}
    assert args[2:] == (999, 5)  # bot_id, bot_top_position
    container.sync_members.execute.assert_awaited_once_with(GUILD, {1: [1]})


async def test_on_ready_backfills_once_despite_reconnects():
    guild = _guild()
    cog, container = make_cog(bot_guilds=[guild])

    await cog.on_ready()
    await cog.on_ready()

    container.sync_guild.execute.assert_awaited_once()


async def test_on_ready_swallows_per_guild_failure():
    good = _guild(guild_id=11)
    bad = _guild(guild_id=12)
    cog, container = make_cog(bot_guilds=[bad, good])

    async def flaky(guild_id, roles, bot_id, bot_top):
        if guild_id == 12:
            raise RuntimeError("boom")

    container.sync_guild.execute = AsyncMock(side_effect=flaky)

    await cog.on_ready()  # не падает целиком из-за одной гильдии

    assert container.sync_guild.execute.await_count == 2


async def test_on_guild_join_backfills_that_guild():
    guild = _guild(guild_id=13)
    cog, container = make_cog()

    await cog.on_guild_join(guild)

    container.sync_guild.execute.assert_awaited_once()
    assert container.sync_guild.execute.await_args.args[0] == 13


async def test_member_holders_exclude_everyone_and_empty():
    everyone = _role(GUILD, default=True)
    role_a = _role(1)
    member_with_roles = _member(1, [everyone, role_a])
    member_no_extra_roles = _member(2, [everyone])  # только @everyone -> не носитель
    guild = _guild(roles=[everyone, role_a], members=[member_with_roles, member_no_extra_roles])
    cog, container = make_cog(bot_guilds=[guild])

    await cog.on_ready()

    container.sync_members.execute.assert_awaited_once_with(GUILD, {1: [1]})


async def test_on_guild_role_create_upserts():
    role = _role(5, name="Новая", position=2)
    role.guild = _guild()
    cog, container = make_cog()

    await cog.on_guild_role_create(role)

    container.upsert_role.execute.assert_awaited_once()
    entity, bot_id, bot_top = container.upsert_role.execute.await_args.args
    assert isinstance(entity, GuildRole)
    assert entity.role_id == 5 and entity.name == "Новая"
    assert (bot_id, bot_top) == (999, 5)


async def test_on_guild_role_update_upserts_after_not_before():
    before = _role(5, name="Старое")
    after = _role(5, name="Новое")
    after.guild = _guild()
    cog, container = make_cog()

    await cog.on_guild_role_update(before, after)

    entity = container.upsert_role.execute.await_args.args[0]
    assert entity.name == "Новое"


async def test_on_guild_role_delete_uses_bot_bounds():
    role = _role(5)
    role.guild = _guild(bot_id=1, bot_top_position=7)
    cog, container = make_cog()

    await cog.on_guild_role_delete(role)

    container.delete_role.execute.assert_awaited_once_with(GUILD, 5, 1, 7)


async def test_on_member_update_syncs_when_roles_changed():
    everyone = _role(GUILD, default=True)
    role_a = _role(1)
    before = SimpleNamespace(roles=[everyone])
    after = SimpleNamespace(id=2, guild=_guild(), roles=[everyone, role_a])
    cog, container = make_cog()

    await cog.on_member_update(before, after)

    container.set_member_roles.execute.assert_awaited_once_with(GUILD, 2, [1])


async def test_on_member_update_skips_when_roles_unchanged():
    everyone = _role(GUILD, default=True)
    roles = [everyone]
    before = SimpleNamespace(roles=roles)
    after = SimpleNamespace(id=2, guild=_guild(), roles=roles)
    cog, container = make_cog()

    await cog.on_member_update(before, after)

    container.set_member_roles.execute.assert_not_awaited()


async def test_on_member_join_syncs_roles():
    everyone = _role(GUILD, default=True)
    role_a = _role(1)
    member = SimpleNamespace(id=3, guild=_guild(), roles=[everyone, role_a])
    cog, container = make_cog()

    await cog.on_member_join(member)

    container.set_member_roles.execute.assert_awaited_once_with(GUILD, 3, [1])


async def test_on_member_remove_deletes_holder():
    member = SimpleNamespace(id=4, guild=_guild())
    cog, container = make_cog()

    await cog.on_member_remove(member)

    container.remove_member.execute.assert_awaited_once_with(GUILD, 4)


async def test_bot_bounds_missing_me_returns_zeros():
    guild = _guild()
    guild.me = None
    cog, container = make_cog(bot_guilds=[guild])

    await cog.on_guild_join(guild)

    assert container.sync_guild.execute.await_args.args[2:] == (0, 0)
