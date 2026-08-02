"""Роли сервера: чтение зеркала (роли + иерархия + счётчики носителей) и
выдача/снятие роли одному участнику через командный мост (реальный Discord
делает бот). CRUD ролей и порядок — отдельной фазой.

`editable` считает бэкенд: роль доступна боту, только если она НИЖЕ его высшей
роли и не managed/@everyone. Настоящая граница проверяется ещё раз в боте
(`command_executor`), панели на слово не верим.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.api.audit import record_audit
from src.api.command_client import run_command
from src.api.container import ApiContainer
from src.api.dependencies import (
    current_session,
    get_container,
    require_guild_manager,
    require_manage_roles,
)
from src.api.permissions_catalog import ADMINISTRATOR_BIT, all_catalog_bits, catalog_json
from src.api.role_presets import get_preset, preset_payload, presets_json
from src.api.security import Session
from src.domain.roles.entities import GuildRole, SavedRoleTemplate, TemplateRole

# потолок сохранённых шаблонов на сервер — чтобы панель не плодила их без предела
_TEMPLATE_CAP = 30

router = APIRouter(prefix="/api/guilds/{guild_id}/roles", tags=["roles"])


class AssignBody(BaseModel):
    role_id: str


class CreateRoleBody(BaseModel):
    name: str
    color: int | None = None  # None/0 = без цвета
    hoist: bool = False
    mentionable: bool = False


class EditRoleBody(BaseModel):
    # только присланные поля уедут в команду (exclude_unset). Права — этап 2.
    name: str | None = None
    color: int | None = None
    hoist: bool | None = None
    mentionable: bool | None = None


class ReorderBody(BaseModel):
    order: list[str]  # id ролей сверху вниз (первая — выше всех)


class PermissionsBody(BaseModel):
    permissions: str  # итоговое битовое поле строкой (не влезает в JS-number)


class BulkBody(BaseModel):
    op: str  # "assign" | "unassign"


class ImportBody(BaseModel):
    # набор ролей из экспорта/шаблона. Права намеренно не переносим — бот создаёт
    # роли без прав (безопасно), совпадения по имени пропускает.
    roles: list[CreateRoleBody]


class AutoRoleBody(BaseModel):
    role_ids: list[str]  # id ролей, выдаваемых новичку при входе; [] = выключить


class InterestRolesBody(BaseModel):
    # интерес анкеты -> id роли (строкой; snowflake не влезает в JS-number).
    # Пустая строка / отсутствие ключа = у интереса нет роли.
    mapping: dict[str, str]


class TemplateSaveBody(BaseModel):
    name: str  # имя сохраняемого шаблона (сохраняем текущие редактируемые роли)


def _template_json(t: SavedRoleTemplate) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "created_at": t.created_at.isoformat(),
        "roles": [
            {"name": r.name, "color": r.color, "hoist": r.hoist, "mentionable": r.mentionable}
            for r in t.roles
        ],
    }


def _editable(role: GuildRole, guild_id: int, bot_top: int | None) -> bool:
    if bot_top is None or role.managed or role.role_id == guild_id:
        return False
    return role.position < bot_top


def _role_json(role: GuildRole, guild_id: int, bot_top: int | None, holders: int | None) -> dict:
    return {
        "id": str(role.role_id),
        "name": role.name,
        "color": role.color,
        "hoist": role.hoist,
        "mentionable": role.mentionable,
        "position": role.position,
        "managed": role.managed,
        "permissions": str(role.permissions),
        "is_default": role.role_id == guild_id,  # @everyone: id == id сервера
        "editable": _editable(role, guild_id, bot_top),
        "holders": holders,
    }


@router.get("")
async def list_roles(
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> dict:
    roles, meta, counts = await container.list_roles.execute(guild_id)
    bot_top = meta.bot_top_position if meta is not None else None
    ordered = sorted(roles, key=lambda r: r.position, reverse=True)
    return {
        "bot_top_position": bot_top,
        "bot_user_id": str(meta.bot_user_id) if meta is not None else None,
        "synced_at": meta.synced_at.isoformat() if meta is not None else None,
        "roles": [_role_json(r, guild_id, bot_top, counts.get(r.role_id, 0)) for r in ordered],
    }


@router.get("/permissions")
async def permissions_catalog(
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Каталог редактируемых прав + маска «что доступно самому боту» (права, что
    есть у ролей бота или @everyone; при Administrator у бота — весь каталог).
    Панель по маске гасит недоступные тумблеры. Administrator в каталог не входит
    как редактируемый — бот его не выдаёт."""
    roles, meta, _counts = await container.list_roles.execute(guild_id)
    bot_mask = 0
    if meta is not None:
        by_id = {r.role_id: r for r in roles}
        everyone = by_id.get(guild_id)  # @everyone: права есть у всех, включая бота
        if everyone is not None:
            bot_mask |= everyone.permissions
        for rid in await container.member_roles.execute(guild_id, meta.bot_user_id):
            r = by_id.get(rid)
            if r is not None:
                bot_mask |= r.permissions
        if bot_mask & ADMINISTRATOR_BIT:
            bot_mask = all_catalog_bits() | ADMINISTRATOR_BIT
    return {
        "categories": catalog_json(),
        "bot_mask": str(bot_mask),
        "admin_bit": str(ADMINISTRATOR_BIT),
    }


@router.post("/import")
async def import_roles(
    body: ImportBody,
    guild_id: int = Depends(require_manage_roles),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Создать недостающие роли из набора (шаблон/экспорт). Права не переносятся;
    роли с совпадающим именем бот пропускает. Кап и создание — на стороне бота."""
    payload = {
        "roles": [
            {"name": r.name, "color": r.color, "hoist": r.hoist, "mentionable": r.mentionable}
            for r in body.roles
        ]
    }
    cmd = await run_command(container, guild_id, "role.import", payload, session.user_id)
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "role.import",
        details={"count": len(body.roles)},
        result=cmd.get("status"),
    )
    return cmd


@router.get("/autorole")
async def get_autorole(
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """id ролей, автоматически выдаваемых новичку при входе (пусто = выключено).
    Отдаём строками — snowflake не влезает в JS-number."""
    ids = container.guild_settings.get(guild_id, "autorole_ids", [])
    return {"role_ids": [str(i) for i in ids]}


@router.put("/autorole")
async def set_autorole(
    body: AutoRoleBody,
    guild_id: int = Depends(require_manage_roles),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Задать автороли при входе. Принимаем только роли, доступные боту (ниже его
    высшей, не managed/@everyone) — бот на входе всё равно перепроверит, но так
    панель не сохранит заведомо бесполезный id."""
    roles, meta, _counts = await container.list_roles.execute(guild_id)
    bot_top = meta.bot_top_position if meta is not None else None
    editable_ids = {r.role_id for r in roles if _editable(r, guild_id, bot_top)}
    try:
        requested = [int(rid) for rid in body.role_ids]
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Некорректный id роли.") from None
    invalid = [rid for rid in requested if rid not in editable_ids]
    if invalid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Некоторые роли недоступны боту (выше его роли, managed или @everyone).",
        )
    # dedup с сохранением порядка
    ordered = list(dict.fromkeys(requested))
    await container.guild_settings.set_many(guild_id, {"autorole_ids": ordered})
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "role.autorole",
        details={"count": len(ordered)},
    )
    return {"role_ids": [str(i) for i in ordered]}


@router.get("/interest-roles")
async def get_interest_roles(
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Маппинг «интерес анкеты → роль». Отдаём список интересов и текущие пары
    (id ролей строками — snowflake). Тоггл интереса в /introduce выдаёт роль."""
    mapping = container.guild_settings.get(guild_id, "interest_roles", {}) or {}
    return {
        "interests": list(container.settings.survey_interest_options),
        "mapping": {k: str(v) for k, v in mapping.items()},
    }


@router.put("/interest-roles")
async def set_interest_roles(
    body: InterestRolesBody,
    guild_id: int = Depends(require_manage_roles),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Задать пары «интерес → роль». Принимаем только известные интересы и роли,
    доступные боту (бот на выдаче всё равно перепроверит). Пустое значение =
    у интереса нет роли."""
    interests = set(container.settings.survey_interest_options)
    roles, meta, _counts = await container.list_roles.execute(guild_id)
    bot_top = meta.bot_top_position if meta is not None else None
    editable_ids = {r.role_id for r in roles if _editable(r, guild_id, bot_top)}
    clean: dict[str, int] = {}
    for interest, raw in body.mapping.items():
        if interest not in interests:
            continue  # неизвестный интерес — игнор
        rid = str(raw).strip()
        if not rid:
            continue  # пусто = без роли
        try:
            role_id = int(rid)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Некорректный id роли.") from None
        if role_id not in editable_ids:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Роль недоступна боту (выше его роли, managed или @everyone).",
            )
        clean[interest] = role_id
    await container.guild_settings.set_many(guild_id, {"interest_roles": clean})
    await record_audit(
        container, guild_id, session.user_id, "role.interest_roles", details={"count": len(clean)}
    )
    return {"mapping": {k: str(v) for k, v in clean.items()}}


@router.get("/templates")
async def list_role_templates(
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Сохранённые наборы ролей сервера (новые сверху)."""
    templates = await container.list_role_templates.execute(guild_id)
    return {"templates": [_template_json(t) for t in templates]}


@router.post("/templates")
async def save_role_template(
    body: TemplateSaveBody,
    guild_id: int = Depends(require_manage_roles),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Сохранить текущие редактируемые роли под именем (upsert по имени). Права не
    сохраняем — шаблон косметический, как экспорт/импорт."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Имя шаблона не может быть пустым.")
    if len(name) > 100:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Имя шаблона длиннее 100 символов.")
    roles, meta, _counts = await container.list_roles.execute(guild_id)
    bot_top = meta.bot_top_position if meta is not None else None
    editable = [
        r
        for r in sorted(roles, key=lambda r: r.position, reverse=True)
        if _editable(r, guild_id, bot_top)
    ]
    if not editable:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Нет редактируемых ролей для сохранения."
        )
    existing = await container.list_role_templates.execute(guild_id)
    # обновление одноимённого шаблона предела не тратит; новый — тратит
    if name not in {t.name for t in existing} and len(existing) >= _TEMPLATE_CAP:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Достигнут предел шаблонов ({_TEMPLATE_CAP}).",
        )
    template_roles = [
        TemplateRole(name=r.name, color=r.color or None, hoist=r.hoist, mentionable=r.mentionable)
        for r in editable
    ]
    saved = await container.save_role_template.execute(guild_id, name, template_roles)
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "role.template_save",
        details={"name": name, "count": len(template_roles)},
    )
    return _template_json(saved)


@router.post("/templates/{template_id}/apply")
async def apply_role_template(
    template_id: int,
    guild_id: int = Depends(require_manage_roles),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Применить сохранённый шаблон: создать недостающие роли через мост
    (role.import — совпадения по имени бот пропустит)."""
    template = await container.get_role_template.execute(guild_id, template_id)
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Шаблон не найден.")
    payload = {
        "roles": [
            {"name": r.name, "color": r.color, "hoist": r.hoist, "mentionable": r.mentionable}
            for r in template.roles
        ]
    }
    cmd = await run_command(container, guild_id, "role.import", payload, session.user_id)
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "role.template_apply",
        target=template_id,
        details={"name": template.name},
        result=cmd.get("status"),
    )
    return cmd


@router.delete("/templates/{template_id}")
async def delete_role_template(
    template_id: int,
    guild_id: int = Depends(require_manage_roles),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    deleted = await container.delete_role_template.execute(guild_id, template_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Шаблон не найден.")
    await record_audit(
        container, guild_id, session.user_id, "role.template_delete", target=template_id
    )
    return {"deleted": True}


@router.get("/presets")
async def list_role_presets(
    guild_id: int = Depends(require_guild_manager),
) -> dict:
    """Готовые наборы ролей С ПРАВАМИ (курируются на сервере, см. role_presets).
    Панель показывает состав и метки прав; применяет по ключу."""
    return {"presets": presets_json()}


@router.post("/presets/{key}/apply")
async def apply_role_preset(
    key: str,
    guild_id: int = Depends(require_manage_roles),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Применить готовый набор: создать недостающие роли сразу с правами через
    мост (role.preset — бот зажимает маску под свои права, Administrator не
    выдаёт, совпадения по имени пропускает). Состав берём с сервера по ключу —
    клиент произвольные биты подсунуть не может."""
    preset = get_preset(key)
    if preset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Набор не найден.")
    cmd = await run_command(
        container, guild_id, "role.preset", preset_payload(preset), session.user_id
    )
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "role.preset_apply",
        details={"key": key, "name": preset["name"], "count": len(preset["roles"])},
        result=cmd.get("status"),
    )
    return cmd


@router.post("")
async def create_role(
    body: CreateRoleBody,
    guild_id: int = Depends(require_manage_roles),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    payload = {
        "name": body.name,
        "color": body.color,
        "hoist": body.hoist,
        "mentionable": body.mentionable,
    }
    cmd = await run_command(container, guild_id, "role.create", payload, session.user_id)
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "role.create",
        details={"name": body.name},
        result=cmd.get("status"),
    )
    return cmd


@router.put("/order")
async def reorder_roles(
    body: ReorderBody,
    guild_id: int = Depends(require_manage_roles),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    cmd = await run_command(
        container, guild_id, "role.reorder", {"order": body.order}, session.user_id
    )
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "role.reorder",
        details={"count": len(body.order)},
        result=cmd.get("status"),
    )
    return cmd


@router.patch("/{role_id}")
async def edit_role(
    role_id: int,
    body: EditRoleBody,
    guild_id: int = Depends(require_manage_roles),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    payload: dict = {"role_id": str(role_id)}
    payload.update(body.model_dump(exclude_unset=True))  # только реально присланные поля
    cmd = await run_command(container, guild_id, "role.edit", payload, session.user_id)
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "role.edit",
        target=role_id,
        details=body.model_dump(exclude_unset=True),
        result=cmd.get("status"),
    )
    return cmd


@router.delete("/{role_id}")
async def delete_role(
    role_id: int,
    guild_id: int = Depends(require_manage_roles),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    cmd = await run_command(
        container, guild_id, "role.delete", {"role_id": str(role_id)}, session.user_id
    )
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "role.delete",
        target=role_id,
        result=cmd.get("status"),
    )
    return cmd


@router.put("/{role_id}/permissions")
async def set_permissions(
    role_id: int,
    body: PermissionsBody,
    guild_id: int = Depends(require_manage_roles),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    # ограждения (Administrator, недоступные боту права) применяет бот —
    # тут только доставляем желаемое битовое поле через мост
    cmd = await run_command(
        container,
        guild_id,
        "role.permissions",
        {"role_id": str(role_id), "permissions": body.permissions},
        session.user_id,
    )
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "role.permissions",
        target=role_id,
        details={"permissions": body.permissions},
        result=cmd.get("status"),
    )
    return cmd


@router.post("/{role_id}/bulk")
async def bulk_role(
    role_id: int,
    body: BulkBody,
    guild_id: int = Depends(require_manage_roles),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Массовая выдача (всем без роли) / снятие (у всех носителей). Бот делает
    синхронно одной командой; на большом сервере панель отдаст «применяется»,
    но бот докрутит. Кому именно — решает бот (панели список не доверяем)."""
    cmd = await run_command(
        container, guild_id, "role.bulk", {"role_id": str(role_id), "op": body.op}, session.user_id
    )
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "role.bulk",
        target=role_id,
        details={"op": body.op},
        result=cmd.get("status"),
    )
    return cmd


@router.get("/members/{user_id}")
async def member_roles(
    user_id: int,
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Роли участника (held) + какие ему можно выдать (assignable — доступные боту
    и ещё не выданные). held показывает и недоступные роли, снять можно лишь
    editable-строки."""
    roles, meta, _counts = await container.list_roles.execute(guild_id)
    bot_top = meta.bot_top_position if meta is not None else None
    held_ids = set(await container.member_roles.execute(guild_id, user_id))
    by_id = {r.role_id: r for r in roles}
    held = [_role_json(by_id[rid], guild_id, bot_top, None) for rid in held_ids if rid in by_id]
    held.sort(key=lambda r: r["position"], reverse=True)
    assignable = [
        _role_json(r, guild_id, bot_top, None)
        for r in sorted(roles, key=lambda r: r.position, reverse=True)
        if _editable(r, guild_id, bot_top) and r.role_id not in held_ids
    ]
    return {"held": held, "assignable": assignable}


@router.post("/members/{user_id}")
async def assign_role(
    user_id: int,
    body: AssignBody,
    guild_id: int = Depends(require_manage_roles),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    cmd = await run_command(
        container,
        guild_id,
        "role.assign",
        {"user_id": str(user_id), "role_id": body.role_id},
        session.user_id,
    )
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "role.assign",
        target=user_id,
        details={"role_id": body.role_id},
        result=cmd.get("status"),
    )
    return cmd


@router.delete("/members/{user_id}/{role_id}")
async def unassign_role(
    user_id: int,
    role_id: int,
    guild_id: int = Depends(require_manage_roles),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    cmd = await run_command(
        container,
        guild_id,
        "role.unassign",
        {"user_id": str(user_id), "role_id": str(role_id)},
        session.user_id,
    )
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "role.unassign",
        target=user_id,
        details={"role_id": str(role_id)},
        result=cmd.get("status"),
    )
    return cmd
