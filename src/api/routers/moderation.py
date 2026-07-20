"""Модерация: чтение варнов/банов + безопасный сброс варнов.

ВАЖНО: панель — отдельный процесс без доступа к Discord-шлюзу. Реальные
бан/анбан/мут делает только бот, поэтому здесь их НЕТ. Доступны лишь действия
без Discord-побочки:
  * список активных временных банов (чтение БД);
  * список варнов участника (чтение БД);
  * сброс варнов (`clearwarns` в боте — чистая БД, ничего в Discord не меняет).
Настоящий бан/мут через панель потребует командного моста к боту (отдельная фаза).
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.api.audit import record_audit
from src.api.command_client import run_command
from src.api.container import ApiContainer
from src.api.dependencies import current_session, get_container, require_guild_manager
from src.api.discord_users import fetch_users
from src.api.security import Session

router = APIRouter(prefix="/api/guilds/{guild_id}/moderation", tags=["moderation"])


def _now() -> datetime:
    return datetime.now(UTC)


class BanBody(BaseModel):
    user_id: str
    minutes: int = Field(ge=1, le=525600)  # до года
    reason: str = ""


class MuteBody(BaseModel):
    user_id: str
    minutes: int = Field(ge=1, le=40320)  # до 28 суток (лимит Discord timeout)
    reason: str = ""


class UserBody(BaseModel):
    user_id: str


@router.get("/bans")
async def bans(
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> list[dict]:
    """Активные временные баны сервера (по сроку). Авторазбан ведёт бот."""
    active = await container.list_bans.execute(guild_id, _now())
    ids = {b.user_id for b in active} | {b.moderator_id for b in active}
    users = await fetch_users(container.settings.discord_token, list(ids))
    return [
        {
            "user_id": str(b.user_id),
            "username": users.get(b.user_id, {}).get("username"),
            "avatar": users.get(b.user_id, {}).get("avatar"),
            "moderator_id": str(b.moderator_id),
            "moderator_name": users.get(b.moderator_id, {}).get("username"),
            "reason": b.reason,
            "expires_at": b.expires_at.isoformat(),
        }
        for b in active
    ]


@router.get("/warns")
async def guild_warns(
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> list[dict]:
    """Кто на сервере сейчас с варнами: участник + число + последний варн."""
    rows = await container.guild_warns.execute(guild_id)
    users = await fetch_users(container.settings.discord_token, [uid for uid, _, _ in rows])
    return [
        {
            "user_id": str(uid),
            "username": users.get(uid, {}).get("username"),
            "avatar": users.get(uid, {}).get("avatar"),
            "count": count,
            "last_at": last_at.isoformat(),
        }
        for uid, count, last_at in rows
    ]


@router.get("/warns/{user_id}")
async def warns(
    user_id: int,
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> list[dict]:
    """Активные варны участника (со свежими до сброса счётчика)."""
    items = await container.get_warns.execute(user_id, guild_id)
    mod_ids = list({w.moderator_id for w in items})
    users = await fetch_users(container.settings.discord_token, mod_ids)
    return [
        {
            "id": w.id,
            "reason": w.reason,
            "moderator_id": str(w.moderator_id),
            "moderator_name": users.get(w.moderator_id, {}).get("username"),
            "created_at": w.created_at.isoformat(),
        }
        for w in items
    ]


@router.delete("/warns/{user_id}")
async def clear_warns(
    user_id: int,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Сброс всех варнов участника. Чистая БД — в Discord ничего не меняется."""
    cleared = await container.clear_warns.execute(user_id, guild_id)
    await record_audit(
        container, guild_id, session.user_id, "warns.clear",
        target=user_id, result=f"снято {cleared}",
    )
    return {"cleared": cleared}


# --- write-действия через командный мост (реальный Discord делает бот) -------


@router.post("/ban")
async def ban(
    body: BanBody,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    cmd = await run_command(
        container,
        guild_id,
        "mod.tempban",
        {"user_id": body.user_id, "minutes": body.minutes, "reason": body.reason},
        session.user_id,
    )
    await record_audit(
        container, guild_id, session.user_id, "mod.ban", target=body.user_id,
        details={"minutes": body.minutes, "reason": body.reason}, result=cmd.get("status"),
    )
    return cmd


@router.post("/unban")
async def unban(
    body: UserBody,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    cmd = await run_command(
        container, guild_id, "mod.unban", {"user_id": body.user_id}, session.user_id
    )
    await record_audit(
        container, guild_id, session.user_id, "mod.unban",
        target=body.user_id, result=cmd.get("status"),
    )
    return cmd


@router.post("/mute")
async def mute(
    body: MuteBody,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    cmd = await run_command(
        container,
        guild_id,
        "mod.mute",
        {"user_id": body.user_id, "minutes": body.minutes, "reason": body.reason},
        session.user_id,
    )
    await record_audit(
        container, guild_id, session.user_id, "mod.mute", target=body.user_id,
        details={"minutes": body.minutes, "reason": body.reason}, result=cmd.get("status"),
    )
    return cmd


@router.post("/unmute")
async def unmute(
    body: UserBody,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    cmd = await run_command(
        container, guild_id, "mod.unmute", {"user_id": body.user_id}, session.user_id
    )
    await record_audit(
        container, guild_id, session.user_id, "mod.unmute",
        target=body.user_id, result=cmd.get("status"),
    )
    return cmd
