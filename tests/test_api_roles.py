"""API-панель, роли сервера: чтение зеркала (список/каталог прав/карточка
участника) + запись через командный мост панель→бот (CRUD/порядок/права/
массовые операции/выдача-снятие одному). Бизнес-правила ограждений проверяет
бот (`command_executor`), тут — что панель верно строит editable/is_default и
доставляет команды с нужным payload.
"""

from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.container import assemble_container
from src.api.security import SESSION_COOKIE, Session, SessionGuild, encode_session
from src.config import Settings
from src.domain.roles.entities import GuildRole, RoleMeta

GUILD = 10
BOT_ID = 999


def make_settings(**over):
    base = {
        "discord_token": "t",
        "discord_client_id": "cid",
        "discord_client_secret": "csec",
        "web_session_secret": "test-session-secret-at-least-32-bytes!!",
        "web_command_wait_seconds": 2.0,
    }
    base.update(over)
    return Settings(_env_file=None, **base)


@pytest.fixture
def container(session_factory):
    c = assemble_container(make_settings(), session_factory.kw["bind"], session_factory)
    c.bot_guilds.prime({GUILD})
    return c


def _cookie(settings, manage_guild_ids) -> str:
    session = Session(
        user_id=1,
        username="u",
        avatar=None,
        guilds=[SessionGuild(id=g, name="G", icon=None) for g in manage_guild_ids],
    )
    return encode_session(settings.web_session_secret, session, 24)


@pytest.fixture
async def client(container):
    app = create_app(container)
    token = _cookie(container.settings, {GUILD})
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE: token},
    ) as c:
        yield c


async def _fake_bot(container, execute):
    """Фоновый «бот»: крутит process_pending с подставным Discord-исполнителем."""
    import asyncio

    from src.infrastructure.commands.bridge import CommandProcessor

    seen = []

    async def executor(cmd):
        seen.append(cmd)
        return await execute(cmd)

    proc = CommandProcessor(container.session_factory, executor)
    stop = asyncio.Event()

    async def pump():
        while not stop.is_set():
            await proc.process_pending()
            await asyncio.sleep(0.01)

    return asyncio.create_task(pump()), stop, seen


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


async def _seed_roles(uow_factory, roles, bot_top_position=10, holders=None):
    async with uow_factory() as uow:
        await uow.roles.replace_guild_roles(GUILD, roles, datetime(2026, 7, 21, 12, 0))
        await uow.roles.set_meta(
            RoleMeta(GUILD, BOT_ID, bot_top_position, datetime(2026, 7, 21, 12, 0))
        )
        if holders:
            await uow.roles.replace_member_roles(GUILD, holders)
        await uow.commit()


# --- права -------------------------------------------------------------------


async def test_list_roles_requires_session(container):
    app = create_app(container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        resp = await anon.get(f"/api/guilds/{GUILD}/roles")
    assert resp.status_code == 401


async def test_list_roles_forbidden_when_cannot_manage(container):
    app = create_app(container)
    token = _cookie(container.settings, {999})
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={SESSION_COOKIE: token}
    ) as c:
        resp = await c.get(f"/api/guilds/{GUILD}/roles")
    assert resp.status_code == 403


# --- список ролей --------------------------------------------------------------


async def test_list_roles_empty_guild(client):
    resp = await client.get(f"/api/guilds/{GUILD}/roles")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"bot_top_position": None, "bot_user_id": None, "synced_at": None, "roles": []}


async def test_list_roles_ordering_and_fields(client, uow_factory):
    everyone = _role(GUILD, name="@everyone", position=0)
    low = _role(1, name="Низкая", position=2)
    high = _role(2, name="Высокая", position=6, permissions=8)
    managed = _role(3, name="Бот-роль", position=8, managed=True)
    above_bot = _role(4, name="Выше бота", position=99)
    await _seed_roles(
        uow_factory,
        [everyone, low, high, managed, above_bot],
        bot_top_position=10,
        holders={7: [1]},
    )

    resp = await client.get(f"/api/guilds/{GUILD}/roles")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bot_top_position"] == 10
    assert body["bot_user_id"] == str(BOT_ID)
    assert body["synced_at"] is not None

    by_id = {r["id"]: r for r in body["roles"]}
    # порядок сверху вниз по позиции
    assert [r["id"] for r in body["roles"]] == ["4", "3", "2", "1", str(GUILD)]

    assert by_id[str(GUILD)]["is_default"] is True
    assert by_id[str(GUILD)]["editable"] is False  # @everyone никогда не editable

    assert by_id["1"]["editable"] is True  # ниже бота, не managed
    assert by_id["1"]["holders"] == 1

    assert by_id["2"]["holders"] == 0
    assert by_id["2"]["permissions"] == "8"  # строкой — не влезает в JS-number

    assert by_id["3"]["editable"] is False  # managed
    assert by_id["4"]["editable"] is False  # position >= bot_top


# --- каталог прав ---------------------------------------------------------------


async def test_permissions_catalog_no_meta_zero_mask(client):
    resp = await client.get(f"/api/guilds/{GUILD}/roles/permissions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bot_mask"] == "0"
    assert any(c["key"] == "moderation" for c in body["categories"])
    assert body["admin_bit"] == str(1 << 3)


async def test_permissions_catalog_combines_everyone_and_bot_roles(client, uow_factory):
    everyone = _role(GUILD, name="@everyone", position=0, permissions=0b0001)
    bot_role = _role(5, name="BotRole", position=9, permissions=0b0110)
    await _seed_roles(uow_factory, [everyone, bot_role], bot_top_position=10, holders={BOT_ID: [5]})

    resp = await client.get(f"/api/guilds/{GUILD}/roles/permissions")
    body = resp.json()
    assert int(body["bot_mask"]) == 0b0111  # @everyone | роли бота


async def test_permissions_catalog_administrator_unlocks_full_catalog(client, uow_factory):
    from src.api.permissions_catalog import ADMINISTRATOR_BIT, all_catalog_bits

    everyone = _role(GUILD, name="@everyone", position=0, permissions=ADMINISTRATOR_BIT)
    await _seed_roles(uow_factory, [everyone], bot_top_position=10)

    resp = await client.get(f"/api/guilds/{GUILD}/roles/permissions")
    body = resp.json()
    assert int(body["bot_mask"]) == all_catalog_bits() | ADMINISTRATOR_BIT


# --- карточка участника: held/assignable ----------------------------------------


async def test_member_roles_held_and_assignable(client, uow_factory):
    everyone = _role(GUILD, name="@everyone", position=0)
    held = _role(1, name="Есть", position=2)
    assignable = _role(2, name="Можно выдать", position=3)
    not_assignable = _role(3, name="Managed", position=4, managed=True)
    await _seed_roles(
        uow_factory,
        [everyone, held, assignable, not_assignable],
        bot_top_position=10,
        holders={5: [1]},
    )

    resp = await client.get(f"/api/guilds/{GUILD}/roles/members/5")
    assert resp.status_code == 200
    body = resp.json()
    assert [r["id"] for r in body["held"]] == ["1"]
    assignable_ids = {r["id"] for r in body["assignable"]}
    assert assignable_ids == {"2"}  # managed и уже выданная — не в списке


# --- запись через мост: CRUD/порядок/права/массово/выдача-снятие ---------------


async def test_create_role_roundtrip_and_audit(client, container):
    async def execute(_cmd):
        return "Создал роль «Новая»."

    task, stop, seen = await _fake_bot(container, execute)
    try:
        resp = await client.post(
            f"/api/guilds/{GUILD}/roles",
            json={"name": "Новая", "color": 255, "hoist": True, "mentionable": False},
        )
    finally:
        stop.set()
        await task
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "done"
    assert seen[0].command_type == "role.create"
    assert seen[0].payload == {"name": "Новая", "color": 255, "hoist": True, "mentionable": False}

    audit = (await client.get(f"/api/guilds/{GUILD}/audit")).json()
    assert audit[0]["action"] == "role.create"
    assert audit[0]["actor_id"] == "1"


async def test_reorder_roles_sends_order(client, container):
    async def execute(_cmd):
        return "Порядок ролей обновлён."

    task, stop, seen = await _fake_bot(container, execute)
    try:
        resp = await client.put(f"/api/guilds/{GUILD}/roles/order", json={"order": ["3", "1", "2"]})
    finally:
        stop.set()
        await task
    assert resp.status_code == 200 and resp.json()["status"] == "done"
    assert seen[0].command_type == "role.reorder"
    assert seen[0].payload == {"order": ["3", "1", "2"]}


async def test_edit_role_sends_only_set_fields(client, container):
    async def execute(_cmd):
        return "Обновил роль."

    task, stop, seen = await _fake_bot(container, execute)
    try:
        resp = await client.patch(f"/api/guilds/{GUILD}/roles/7", json={"hoist": True})
    finally:
        stop.set()
        await task
    assert resp.status_code == 200
    assert seen[0].command_type == "role.edit"
    assert seen[0].payload == {"role_id": "7", "hoist": True}  # name/color/mentionable не присланы


async def test_delete_role_roundtrip_and_audit(client, container):
    async def execute(_cmd):
        return "Удалил роль."

    task, stop, seen = await _fake_bot(container, execute)
    try:
        resp = await client.delete(f"/api/guilds/{GUILD}/roles/7")
    finally:
        stop.set()
        await task
    assert resp.status_code == 200
    assert seen[0].command_type == "role.delete"
    assert seen[0].payload == {"role_id": "7"}
    audit = (await client.get(f"/api/guilds/{GUILD}/audit")).json()
    assert audit[0]["action"] == "role.delete" and audit[0]["target"] == "7"


async def test_set_permissions_roundtrip(client, container):
    async def execute(_cmd):
        return "Обновил права роли."

    task, stop, seen = await _fake_bot(container, execute)
    try:
        resp = await client.put(
            f"/api/guilds/{GUILD}/roles/7/permissions", json={"permissions": "12345678901234"}
        )
    finally:
        stop.set()
        await task
    assert resp.status_code == 200
    assert seen[0].command_type == "role.permissions"
    assert seen[0].payload == {"role_id": "7", "permissions": "12345678901234"}


async def test_bulk_role_roundtrip(client, container):
    async def execute(_cmd):
        return "Выдал роль «X»: 3 чел."

    task, stop, seen = await _fake_bot(container, execute)
    try:
        resp = await client.post(f"/api/guilds/{GUILD}/roles/7/bulk", json={"op": "assign"})
    finally:
        stop.set()
        await task
    assert resp.status_code == 200
    assert seen[0].command_type == "role.bulk"
    assert seen[0].payload == {"role_id": "7", "op": "assign"}


async def test_assign_role_roundtrip(client, container):
    async def execute(_cmd):
        return "Выдал роль."

    task, stop, seen = await _fake_bot(container, execute)
    try:
        resp = await client.post(f"/api/guilds/{GUILD}/roles/members/5", json={"role_id": "7"})
    finally:
        stop.set()
        await task
    assert resp.status_code == 200
    assert seen[0].command_type == "role.assign"
    assert seen[0].payload == {"user_id": "5", "role_id": "7"}


async def test_unassign_role_roundtrip(client, container):
    async def execute(_cmd):
        return "Снял роль."

    task, stop, seen = await _fake_bot(container, execute)
    try:
        resp = await client.delete(f"/api/guilds/{GUILD}/roles/members/5/7")
    finally:
        stop.set()
        await task
    assert resp.status_code == 200
    assert seen[0].command_type == "role.unassign"
    assert seen[0].payload == {"user_id": "5", "role_id": "7"}


# --- импорт ролей (шаблоны/экспорт) --------------------------------------------


async def test_import_roles_roundtrip_and_audit(client, container):
    async def execute(_cmd):
        return "Создано ролей: 2."

    task, stop, seen = await _fake_bot(container, execute)
    try:
        resp = await client.post(
            f"/api/guilds/{GUILD}/roles/import",
            json={
                "roles": [
                    {"name": "A", "color": 255, "hoist": True, "mentionable": False},
                    {"name": "B"},
                ]
            },
        )
    finally:
        stop.set()
        await task
    assert resp.status_code == 200 and resp.json()["status"] == "done"
    assert seen[0].command_type == "role.import"
    assert seen[0].payload == {
        "roles": [
            {"name": "A", "color": 255, "hoist": True, "mentionable": False},
            {"name": "B", "color": None, "hoist": False, "mentionable": False},
        ]
    }
    audit = (await client.get(f"/api/guilds/{GUILD}/audit")).json()
    assert audit[0]["action"] == "role.import"


# --- автороли при входе ---------------------------------------------------------


async def test_get_autorole_default_empty(client):
    resp = await client.get(f"/api/guilds/{GUILD}/roles/autorole")
    assert resp.status_code == 200
    assert resp.json() == {"role_ids": []}


async def test_set_autorole_valid_roundtrips(client, uow_factory):
    everyone = _role(GUILD, name="@everyone", position=0)
    editable = _role(1, name="Базовая", position=2)
    await _seed_roles(uow_factory, [everyone, editable], bot_top_position=10)

    resp = await client.put(f"/api/guilds/{GUILD}/roles/autorole", json={"role_ids": ["1"]})
    assert resp.status_code == 200
    assert resp.json() == {"role_ids": ["1"]}
    # и читается обратно из настроек
    got = await client.get(f"/api/guilds/{GUILD}/roles/autorole")
    assert got.json() == {"role_ids": ["1"]}

    audit = (await client.get(f"/api/guilds/{GUILD}/audit")).json()
    assert audit[0]["action"] == "role.autorole"


async def test_set_autorole_rejects_non_editable(client, uow_factory):
    everyone = _role(GUILD, name="@everyone", position=0)
    above = _role(4, name="Выше бота", position=99)
    await _seed_roles(uow_factory, [everyone, above], bot_top_position=10)

    resp = await client.put(f"/api/guilds/{GUILD}/roles/autorole", json={"role_ids": ["4"]})
    assert resp.status_code == 422


async def test_set_autorole_dedups(client, uow_factory):
    everyone = _role(GUILD, name="@everyone", position=0)
    editable = _role(1, name="Базовая", position=2)
    await _seed_roles(uow_factory, [everyone, editable], bot_top_position=10)

    resp = await client.put(f"/api/guilds/{GUILD}/roles/autorole", json={"role_ids": ["1", "1"]})
    assert resp.status_code == 200
    assert resp.json() == {"role_ids": ["1"]}


# --- сохранённые шаблоны ролей ---------------------------------------------------


async def test_list_templates_empty(client):
    resp = await client.get(f"/api/guilds/{GUILD}/roles/templates")
    assert resp.status_code == 200
    assert resp.json() == {"templates": []}


async def test_save_template_from_current_editable_roles(client, uow_factory):
    everyone = _role(GUILD, name="@everyone", position=0)
    editable = _role(1, name="Базовая", position=2)
    above = _role(4, name="Выше бота", position=99)
    await _seed_roles(uow_factory, [everyone, editable, above], bot_top_position=10)

    resp = await client.post(f"/api/guilds/{GUILD}/roles/templates", json={"name": "Набор"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Набор"
    assert [r["name"] for r in body["roles"]] == ["Базовая"]  # только editable, без @everyone/выше

    lst = (await client.get(f"/api/guilds/{GUILD}/roles/templates")).json()
    assert [t["name"] for t in lst["templates"]] == ["Набор"]

    audit = (await client.get(f"/api/guilds/{GUILD}/audit")).json()
    assert audit[0]["action"] == "role.template_save"


async def test_save_template_no_editable_roles_422(client, uow_factory):
    everyone = _role(GUILD, name="@everyone", position=0)
    above = _role(4, name="Выше бота", position=99)
    await _seed_roles(uow_factory, [everyone, above], bot_top_position=10)
    resp = await client.post(f"/api/guilds/{GUILD}/roles/templates", json={"name": "X"})
    assert resp.status_code == 422


async def test_save_template_empty_name_400(client, uow_factory):
    everyone = _role(GUILD, name="@everyone", position=0)
    editable = _role(1, name="Базовая", position=2)
    await _seed_roles(uow_factory, [everyone, editable], bot_top_position=10)
    resp = await client.post(f"/api/guilds/{GUILD}/roles/templates", json={"name": "   "})
    assert resp.status_code == 400


async def test_apply_template_roundtrip(client, container, uow_factory):
    everyone = _role(GUILD, name="@everyone", position=0)
    editable = _role(1, name="Базовая", position=2)
    await _seed_roles(uow_factory, [everyone, editable], bot_top_position=10)
    saved = (
        await client.post(f"/api/guilds/{GUILD}/roles/templates", json={"name": "Набор"})
    ).json()
    tid = saved["id"]

    async def execute(_cmd):
        return "Создано ролей: 1."

    task, stop, seen = await _fake_bot(container, execute)
    try:
        resp = await client.post(f"/api/guilds/{GUILD}/roles/templates/{tid}/apply")
    finally:
        stop.set()
        await task
    assert resp.status_code == 200 and resp.json()["status"] == "done"
    assert seen[0].command_type == "role.import"
    assert seen[0].payload["roles"][0]["name"] == "Базовая"

    audit = (await client.get(f"/api/guilds/{GUILD}/audit")).json()
    assert audit[0]["action"] == "role.template_apply"


async def test_apply_template_not_found_404(client):
    resp = await client.post(f"/api/guilds/{GUILD}/roles/templates/999/apply")
    assert resp.status_code == 404


async def test_delete_template_then_gone(client, uow_factory):
    everyone = _role(GUILD, name="@everyone", position=0)
    editable = _role(1, name="Базовая", position=2)
    await _seed_roles(uow_factory, [everyone, editable], bot_top_position=10)
    saved = (await client.post(f"/api/guilds/{GUILD}/roles/templates", json={"name": "T"})).json()
    tid = saved["id"]

    resp = await client.delete(f"/api/guilds/{GUILD}/roles/templates/{tid}")
    assert resp.status_code == 200 and resp.json()["deleted"] is True
    # повторное удаление — 404
    resp2 = await client.delete(f"/api/guilds/{GUILD}/roles/templates/{tid}")
    assert resp2.status_code == 404
    lst = (await client.get(f"/api/guilds/{GUILD}/roles/templates")).json()
    assert lst["templates"] == []


async def test_command_failure_surfaces_not_as_http_error(client, container):
    from src.infrastructure.commands.bridge import CommandError

    async def execute(_cmd):
        raise CommandError("Нет права Manage Roles.")

    task, stop, _seen = await _fake_bot(container, execute)
    try:
        resp = await client.delete(f"/api/guilds/{GUILD}/roles/7")
    finally:
        stop.set()
        await task
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed" and data["result"] == "Нет права Manage Roles."
