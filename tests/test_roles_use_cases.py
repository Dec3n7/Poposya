"""Use-cases зеркала ролей: делегирование в репозиторий + связка с мета
(граница бота обновляется вместе с каждым изменением ролей) поверх реальной БД."""

from datetime import datetime

from src.application.roles.use_cases import (
    DeleteRoleUseCase,
    ListRolesUseCase,
    MemberRolesUseCase,
    RemoveMemberUseCase,
    SetMemberRolesUseCase,
    SyncGuildRolesUseCase,
    SyncMembersUseCase,
    UpsertRoleUseCase,
)
from src.domain.roles.entities import GuildRole

GUILD = 10
BOT_ID = 999


def _role(role_id, name="Role", position=1, managed=False, permissions=0):
    return GuildRole(
        guild_id=GUILD,
        role_id=role_id,
        name=name,
        color=0,
        hoist=False,
        mentionable=False,
        position=position,
        managed=managed,
        permissions=permissions,
    )


async def test_sync_guild_roles_sets_roles_and_meta(uow_factory):
    await SyncGuildRolesUseCase(uow_factory).execute(GUILD, [_role(1), _role(2)], BOT_ID, 5)

    roles, meta, counts = await ListRolesUseCase(uow_factory).execute(GUILD)
    assert {r.role_id for r in roles} == {1, 2}
    assert meta is not None
    assert (meta.bot_user_id, meta.bot_top_position) == (BOT_ID, 5)
    assert isinstance(meta.synced_at, datetime)
    assert counts == {}  # носителей ещё нет


async def test_sync_guild_roles_replaces_previous_backfill(uow_factory):
    await SyncGuildRolesUseCase(uow_factory).execute(GUILD, [_role(1), _role(2)], BOT_ID, 5)
    await SyncGuildRolesUseCase(uow_factory).execute(GUILD, [_role(1)], BOT_ID, 5)  # роль 2 удалена

    roles, _meta, _counts = await ListRolesUseCase(uow_factory).execute(GUILD)
    assert {r.role_id for r in roles} == {1}


async def test_upsert_role_updates_role_and_bumps_meta(uow_factory):
    await SyncGuildRolesUseCase(uow_factory).execute(GUILD, [_role(1, name="Старое")], BOT_ID, 5)

    await UpsertRoleUseCase(uow_factory).execute(_role(1, name="Новое"), BOT_ID, 8)

    roles, meta, _counts = await ListRolesUseCase(uow_factory).execute(GUILD)
    assert roles[0].name == "Новое"
    assert meta.bot_top_position == 8  # роли сдвинулись — граница бота тоже обновилась


async def test_upsert_role_inserts_new_role(uow_factory):
    await UpsertRoleUseCase(uow_factory).execute(_role(3, name="Свежая"), BOT_ID, 5)

    roles, _meta, _counts = await ListRolesUseCase(uow_factory).execute(GUILD)
    assert len(roles) == 1 and roles[0].role_id == 3


async def test_delete_role_removes_role_and_updates_meta(uow_factory):
    await SyncGuildRolesUseCase(uow_factory).execute(GUILD, [_role(1), _role(2)], BOT_ID, 5)

    await DeleteRoleUseCase(uow_factory).execute(GUILD, 1, BOT_ID, 4)

    roles, meta, _counts = await ListRolesUseCase(uow_factory).execute(GUILD)
    assert {r.role_id for r in roles} == {2}
    assert meta.bot_top_position == 4


async def test_delete_role_strips_holders_too(uow_factory):
    await SyncGuildRolesUseCase(uow_factory).execute(GUILD, [_role(1)], BOT_ID, 5)
    await SyncMembersUseCase(uow_factory).execute(GUILD, {7: [1]})

    await DeleteRoleUseCase(uow_factory).execute(GUILD, 1, BOT_ID, 5)

    assert await MemberRolesUseCase(uow_factory).execute(GUILD, 7) == []


async def test_list_roles_empty_guild_has_no_meta(uow_factory):
    roles, meta, counts = await ListRolesUseCase(uow_factory).execute(GUILD)
    assert roles == [] and meta is None and counts == {}


async def test_list_roles_holder_counts(uow_factory):
    await SyncGuildRolesUseCase(uow_factory).execute(GUILD, [_role(1)], BOT_ID, 5)
    await SyncMembersUseCase(uow_factory).execute(GUILD, {1: [1], 2: [1], 3: [1]})

    _roles, _meta, counts = await ListRolesUseCase(uow_factory).execute(GUILD)
    assert counts == {1: 3}


async def test_sync_members_backfill_then_member_roles(uow_factory):
    await SyncMembersUseCase(uow_factory).execute(GUILD, {1: [10, 11], 2: [10]})

    assert set(await MemberRolesUseCase(uow_factory).execute(GUILD, 1)) == {10, 11}
    assert await MemberRolesUseCase(uow_factory).execute(GUILD, 2) == [10]


async def test_sync_members_replaces_previous_backfill(uow_factory):
    await SyncMembersUseCase(uow_factory).execute(GUILD, {1: [10]})
    await SyncMembersUseCase(uow_factory).execute(GUILD, {2: [10]})  # 1 вышел из носителей

    assert await MemberRolesUseCase(uow_factory).execute(GUILD, 1) == []
    assert await MemberRolesUseCase(uow_factory).execute(GUILD, 2) == [10]


async def test_set_member_roles_touches_only_that_member(uow_factory):
    await SyncMembersUseCase(uow_factory).execute(GUILD, {1: [10], 2: [10]})

    await SetMemberRolesUseCase(uow_factory).execute(GUILD, 1, [10, 11])

    assert set(await MemberRolesUseCase(uow_factory).execute(GUILD, 1)) == {10, 11}
    assert await MemberRolesUseCase(uow_factory).execute(GUILD, 2) == [10]


async def test_remove_member_clears_holder(uow_factory):
    await SyncMembersUseCase(uow_factory).execute(GUILD, {1: [10], 2: [10]})

    await RemoveMemberUseCase(uow_factory).execute(GUILD, 1)

    assert await MemberRolesUseCase(uow_factory).execute(GUILD, 1) == []
    assert await MemberRolesUseCase(uow_factory).execute(GUILD, 2) == [10]


async def test_member_roles_unknown_member_is_empty(uow_factory):
    assert await MemberRolesUseCase(uow_factory).execute(GUILD, 404) == []
