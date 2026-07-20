"""Роли сервера: чтение зеркала (роли + иерархия + счётчики носителей) и
выдача/снятие роли одному участнику через командный мост (реальный Discord
делает бот). CRUD ролей и порядок — отдельной фазой.

`editable` считает бэкенд: роль доступна боту, только если она НИЖЕ его высшей
роли и не managed/@everyone. Настоящая граница проверяется ещё раз в боте
(`command_executor`), панели на слово не верим.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.audit import record_audit
from src.api.command_client import run_command
from src.api.container import ApiContainer
from src.api.dependencies import current_session, get_container, require_guild_manager
from src.api.security import Session
from src.domain.roles.entities import GuildRole

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


@router.post("")
async def create_role(
    body: CreateRoleBody,
    guild_id: int = Depends(require_guild_manager),
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
        container, guild_id, session.user_id, "role.create",
        details={"name": body.name}, result=cmd.get("status"),
    )
    return cmd


@router.put("/order")
async def reorder_roles(
    body: ReorderBody,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    cmd = await run_command(
        container, guild_id, "role.reorder", {"order": body.order}, session.user_id
    )
    await record_audit(
        container, guild_id, session.user_id, "role.reorder",
        details={"count": len(body.order)}, result=cmd.get("status"),
    )
    return cmd


@router.patch("/{role_id}")
async def edit_role(
    role_id: int,
    body: EditRoleBody,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    payload: dict = {"role_id": str(role_id)}
    payload.update(body.model_dump(exclude_unset=True))  # только реально присланные поля
    cmd = await run_command(container, guild_id, "role.edit", payload, session.user_id)
    await record_audit(
        container, guild_id, session.user_id, "role.edit",
        target=role_id, details=body.model_dump(exclude_unset=True), result=cmd.get("status"),
    )
    return cmd


@router.delete("/{role_id}")
async def delete_role(
    role_id: int,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    cmd = await run_command(
        container, guild_id, "role.delete", {"role_id": str(role_id)}, session.user_id
    )
    await record_audit(
        container, guild_id, session.user_id, "role.delete",
        target=role_id, result=cmd.get("status"),
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
    held = [
        _role_json(by_id[rid], guild_id, bot_top, None) for rid in held_ids if rid in by_id
    ]
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
    guild_id: int = Depends(require_guild_manager),
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
        container, guild_id, session.user_id, "role.assign",
        target=user_id, details={"role_id": body.role_id}, result=cmd.get("status"),
    )
    return cmd


@router.delete("/members/{user_id}/{role_id}")
async def unassign_role(
    user_id: int,
    role_id: int,
    guild_id: int = Depends(require_guild_manager),
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
        container, guild_id, session.user_id, "role.unassign",
        target=user_id, details={"role_id": str(role_id)}, result=cmd.get("status"),
    )
    return cmd
