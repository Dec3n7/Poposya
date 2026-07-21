"""SqlAlchemyRoleRepository: зеркало ролей Discord + носители (поверх реального
SQLite/Postgres через uow_factory). replace_* — полный бэкфилл (снести и
переложить), upsert/set_* — точечные правки по gateway-событиям."""

from datetime import datetime, timedelta

from src.domain.roles.entities import GuildRole, RoleMeta, TemplateRole

GUILD = 10
NOW = datetime(2026, 7, 21, 12, 0)


def _role(role_id, guild_id=GUILD, name="Role", position=1, managed=False, color=0, permissions=0):
    return GuildRole(
        guild_id=guild_id,
        role_id=role_id,
        name=name,
        color=color,
        hoist=False,
        mentionable=False,
        position=position,
        managed=managed,
        permissions=permissions,
    )


async def test_replace_guild_roles_then_list(uow_factory):
    async with uow_factory() as uow:
        await uow.roles.replace_guild_roles(GUILD, [_role(1), _role(2, name="Второй")], NOW)
        await uow.commit()
    async with uow_factory() as uow:
        roles = await uow.roles.list_roles(GUILD)
    assert {r.role_id for r in roles} == {1, 2}


async def test_replace_guild_roles_drops_roles_removed_in_discord(uow_factory):
    async with uow_factory() as uow:
        await uow.roles.replace_guild_roles(GUILD, [_role(1), _role(2)], NOW)
        await uow.commit()
    # бэкфилл после рестарта: роль 2 удалили в Discord, пока бот лежал
    async with uow_factory() as uow:
        await uow.roles.replace_guild_roles(GUILD, [_role(1)], NOW)
        await uow.commit()
    async with uow_factory() as uow:
        roles = await uow.roles.list_roles(GUILD)
    assert {r.role_id for r in roles} == {1}


async def test_replace_guild_roles_scoped_to_guild(uow_factory):
    other_guild = GUILD + 1
    async with uow_factory() as uow:
        await uow.roles.replace_guild_roles(GUILD, [_role(1)], NOW)
        await uow.roles.replace_guild_roles(other_guild, [_role(2, guild_id=other_guild)], NOW)
        await uow.commit()
    async with uow_factory() as uow:
        await uow.roles.replace_guild_roles(GUILD, [], NOW)  # у сервера GUILD роли снесли
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.roles.list_roles(GUILD) == []
        other_roles = await uow.roles.list_roles(other_guild)
    assert {r.role_id for r in other_roles} == {2}  # соседний сервер не задет


async def test_upsert_role_inserts_new(uow_factory):
    async with uow_factory() as uow:
        await uow.roles.upsert_role(_role(1, name="Новая"), NOW)
        await uow.commit()
    async with uow_factory() as uow:
        roles = await uow.roles.list_roles(GUILD)
    assert len(roles) == 1 and roles[0].name == "Новая"


async def test_upsert_role_updates_existing_without_duplicating(uow_factory):
    async with uow_factory() as uow:
        await uow.roles.upsert_role(_role(1, name="Старое", position=1), NOW)
        await uow.commit()
    async with uow_factory() as uow:
        await uow.roles.upsert_role(_role(1, name="Новое", position=5), NOW)
        await uow.commit()
    async with uow_factory() as uow:
        roles = await uow.roles.list_roles(GUILD)
    assert len(roles) == 1
    assert roles[0].name == "Новое" and roles[0].position == 5


async def test_delete_role_removes_from_mirror(uow_factory):
    async with uow_factory() as uow:
        await uow.roles.replace_guild_roles(GUILD, [_role(1), _role(2)], NOW)
        await uow.commit()
    async with uow_factory() as uow:
        await uow.roles.delete_role(GUILD, 1)
        await uow.commit()
    async with uow_factory() as uow:
        roles = await uow.roles.list_roles(GUILD)
    assert {r.role_id for r in roles} == {2}


async def test_delete_role_strips_it_from_holders(uow_factory):
    async with uow_factory() as uow:
        await uow.roles.replace_member_roles(GUILD, {1: [5, 6], 2: [5]})
        await uow.commit()
    async with uow_factory() as uow:
        await uow.roles.delete_role(GUILD, 5)
        await uow.commit()
    async with uow_factory() as uow:
        user1_roles = await uow.roles.member_role_ids(GUILD, 1)
        user2_roles = await uow.roles.member_role_ids(GUILD, 2)
    assert user1_roles == [6]  # роль 5 снята со всех носителей
    assert user2_roles == []


async def test_get_meta_missing_returns_none(uow_factory):
    async with uow_factory() as uow:
        assert await uow.roles.get_meta(GUILD) is None


async def test_set_meta_then_get_meta(uow_factory):
    async with uow_factory() as uow:
        await uow.roles.set_meta(
            RoleMeta(GUILD, bot_user_id=999, bot_top_position=7, synced_at=NOW)
        )
        await uow.commit()
    async with uow_factory() as uow:
        meta = await uow.roles.get_meta(GUILD)
    assert meta is not None
    assert (meta.bot_user_id, meta.bot_top_position) == (999, 7)


async def test_set_meta_upserts_not_duplicates(uow_factory):
    async with uow_factory() as uow:
        await uow.roles.set_meta(RoleMeta(GUILD, 999, 7, NOW))
        await uow.commit()
    async with uow_factory() as uow:
        await uow.roles.set_meta(RoleMeta(GUILD, 999, 12, NOW))  # роли подвинули
        await uow.commit()
    async with uow_factory() as uow:
        meta = await uow.roles.get_meta(GUILD)
    assert meta.bot_top_position == 12


async def test_replace_member_roles_backfill_and_read(uow_factory):
    async with uow_factory() as uow:
        await uow.roles.replace_member_roles(GUILD, {1: [10, 11], 2: [10]})
        await uow.commit()
    async with uow_factory() as uow:
        assert set(await uow.roles.member_role_ids(GUILD, 1)) == {10, 11}
        assert await uow.roles.member_role_ids(GUILD, 2) == [10]


async def test_replace_member_roles_wipes_previous_backfill(uow_factory):
    async with uow_factory() as uow:
        await uow.roles.replace_member_roles(GUILD, {1: [10]})
        await uow.commit()
    async with uow_factory() as uow:
        await uow.roles.replace_member_roles(GUILD, {2: [10]})  # 1 больше не носитель
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.roles.member_role_ids(GUILD, 1) == []
        assert await uow.roles.member_role_ids(GUILD, 2) == [10]


async def test_set_member_roles_touches_only_that_member(uow_factory):
    async with uow_factory() as uow:
        await uow.roles.replace_member_roles(GUILD, {1: [10], 2: [10]})
        await uow.commit()
    async with uow_factory() as uow:
        await uow.roles.set_member_roles(GUILD, 1, [10, 11])
        await uow.commit()
    async with uow_factory() as uow:
        assert set(await uow.roles.member_role_ids(GUILD, 1)) == {10, 11}
        assert await uow.roles.member_role_ids(GUILD, 2) == [10]  # соседа не задело


async def test_set_member_roles_empty_list_clears_holder(uow_factory):
    async with uow_factory() as uow:
        await uow.roles.replace_member_roles(GUILD, {1: [10]})
        await uow.commit()
    async with uow_factory() as uow:
        await uow.roles.set_member_roles(GUILD, 1, [])
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.roles.member_role_ids(GUILD, 1) == []


async def test_delete_member_removes_only_that_member(uow_factory):
    async with uow_factory() as uow:
        await uow.roles.replace_member_roles(GUILD, {1: [10], 2: [10]})
        await uow.commit()
    async with uow_factory() as uow:
        await uow.roles.delete_member(GUILD, 1)
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.roles.member_role_ids(GUILD, 1) == []
        assert await uow.roles.member_role_ids(GUILD, 2) == [10]


async def test_holder_counts(uow_factory):
    async with uow_factory() as uow:
        await uow.roles.replace_member_roles(GUILD, {1: [10, 11], 2: [10], 3: [10]})
        await uow.commit()
    async with uow_factory() as uow:
        counts = await uow.roles.holder_counts(GUILD)
    assert counts == {10: 3, 11: 1}


async def test_holder_counts_empty_guild(uow_factory):
    async with uow_factory() as uow:
        assert await uow.roles.holder_counts(GUILD) == {}


# --- сохранённые шаблоны ролей ---


def _tr(name, color=None, hoist=False, mentionable=False):
    return TemplateRole(name=name, color=color, hoist=hoist, mentionable=mentionable)


async def test_save_and_list_template(uow_factory):
    async with uow_factory() as uow:
        saved = await uow.roles.save_template(
            GUILD, "Набор", [_tr("A", color=255, hoist=True), _tr("B")], NOW
        )
        await uow.commit()
        assert saved.id > 0
        assert saved.name == "Набор"
        assert [r.name for r in saved.roles] == ["A", "B"]
        assert saved.roles[0].color == 255 and saved.roles[0].hoist is True
    async with uow_factory() as uow:
        templates = await uow.roles.list_templates(GUILD)
    assert len(templates) == 1 and templates[0].name == "Набор"


async def test_save_template_upserts_by_name(uow_factory):
    async with uow_factory() as uow:
        first = await uow.roles.save_template(GUILD, "T", [_tr("A")], NOW)
        await uow.commit()
        first_id = first.id
    async with uow_factory() as uow:
        second = await uow.roles.save_template(GUILD, "T", [_tr("B"), _tr("C")], NOW)
        await uow.commit()
        assert second.id == first_id  # тот же ряд, не дубль
    async with uow_factory() as uow:
        templates = await uow.roles.list_templates(GUILD)
    assert len(templates) == 1
    assert [r.name for r in templates[0].roles] == ["B", "C"]


async def test_get_template_scoped_to_guild(uow_factory):
    async with uow_factory() as uow:
        saved = await uow.roles.save_template(GUILD, "T", [_tr("A")], NOW)
        await uow.commit()
        tid = saved.id
    async with uow_factory() as uow:
        assert await uow.roles.get_template(GUILD, tid) is not None
        assert await uow.roles.get_template(GUILD + 1, tid) is None  # чужой сервер не видит


async def test_delete_template(uow_factory):
    async with uow_factory() as uow:
        saved = await uow.roles.save_template(GUILD, "T", [_tr("A")], NOW)
        await uow.commit()
        tid = saved.id
    async with uow_factory() as uow:
        assert await uow.roles.delete_template(GUILD, tid) is True
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.roles.delete_template(GUILD, tid) is False  # уже нет
        assert await uow.roles.list_templates(GUILD) == []


async def test_list_templates_scoped_and_newest_first(uow_factory):
    async with uow_factory() as uow:
        await uow.roles.save_template(GUILD, "Old", [_tr("A")], NOW)
        await uow.roles.save_template(GUILD, "New", [_tr("B")], NOW + timedelta(hours=1))
        await uow.roles.save_template(GUILD + 1, "Other", [_tr("C")], NOW)  # соседний сервер
        await uow.commit()
    async with uow_factory() as uow:
        templates = await uow.roles.list_templates(GUILD)
    assert [t.name for t in templates] == ["New", "Old"]  # новые сверху, чужой не виден
