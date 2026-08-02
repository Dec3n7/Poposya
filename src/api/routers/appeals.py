"""Апелляции наказаний в панели: список открытых + принять/отклонить.

Список — чтение БД (обогащаем именами через Discord API). Принять/отклонить
идёт через командный мост: реальное снятие наказания (разбан/анмут) и ЛС
участнику делает бот (у панели нет доступа к шлюзу), он же меняет статус в БД.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.audit import record_audit
from src.api.command_client import run_command
from src.api.container import ApiContainer
from src.api.dependencies import (
    current_session,
    get_container,
    require_any_moderator,
    require_guild_manager,
)
from src.api.discord_users import fetch_users
from src.api.security import Session

router = APIRouter(prefix="/api/guilds/{guild_id}/appeals", tags=["appeals"])


class AppealDecisionBody(BaseModel):
    appeal_id: int


@router.get("")
async def list_appeals(
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> list[dict]:
    """Открытые (pending) апелляции сервера, старые→новые."""
    appeals = await container.list_pending_appeals.execute(guild_id)
    users = await fetch_users(container.settings.discord_token, [a.user_id for a in appeals])
    return [
        {
            "id": a.id,
            "user_id": str(a.user_id),
            "username": users.get(a.user_id, {}).get("username"),
            "avatar": users.get(a.user_id, {}).get("avatar"),
            "action": a.action,
            "text": a.text,
            "original_reason": a.original_reason,
            "created_at": a.created_at.isoformat(),
        }
        for a in appeals
    ]


async def _decide(
    guild_id: int, appeal_id: int, approve: bool, session: Session, container: ApiContainer
) -> dict:
    cmd_type = "appeal.approve" if approve else "appeal.reject"
    cmd = await run_command(
        container, guild_id, cmd_type, {"appeal_id": appeal_id}, session.user_id
    )
    await record_audit(
        container,
        guild_id,
        session.user_id,
        cmd_type,
        target=appeal_id,
        result=cmd.get("status"),
    )
    return cmd


@router.post("/{appeal_id}/approve")
async def approve_appeal(
    appeal_id: int,
    guild_id: int = Depends(require_any_moderator),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    return await _decide(guild_id, appeal_id, True, session, container)


@router.post("/{appeal_id}/reject")
async def reject_appeal(
    appeal_id: int,
    guild_id: int = Depends(require_any_moderator),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    return await _decide(guild_id, appeal_id, False, session, container)
