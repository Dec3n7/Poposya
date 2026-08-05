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
from src.api.dependencies import (
    current_session,
    get_container,
    require_ban_members,
    require_guild_manager,
    require_kick_members,
    require_moderate_members,
)
from src.api.discord_members import fetch_guild_member
from src.api.discord_users import fetch_users
from src.api.security import Session
from src.application.banwatch.dto import CrossBanReport

router = APIRouter(prefix="/api/guilds/{guild_id}/moderation", tags=["moderation"])

# сколько отмеченных кандидатов максимум проверять на членство за один запрос
_CROSSBAN_MAX = 50


def _records_json(report: CrossBanReport) -> list[dict]:
    # Причину бана (свободный текст чужого сервера) НАРУЖУ не отдаём: она может
    # нести чувствительное (имена/обвинения/личные данные), а межсерверная
    # видимость — уже сама по себе много. Оставляем «где и когда» — этого хватает
    # для решения модератора, без утечки формулировок между сообществами.
    return [
        {
            "guild_id": str(r.guild_id),
            "guild_name": r.guild_name,
            "banned_at": r.banned_at.isoformat() if r.banned_at else None,
        }
        for r in report.records
    ]


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


class KickBody(BaseModel):
    user_id: str
    reason: str = ""


class PermBanBody(BaseModel):
    user_id: str
    reason: str = ""
    delete_days: int = Field(0, ge=0, le=7)


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


@router.get("/history/{user_id}")
async def history(
    user_id: int,
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> list[dict]:
    """Единый журнал действий модерации по участнику (бот + панель), свежие сверху."""
    cases = await container.user_history.execute(guild_id, user_id, limit=50)
    mod_ids = [c.moderator_id for c in cases if c.moderator_id]
    users = await fetch_users(container.settings.discord_token, list(set(mod_ids)))
    return [
        {
            "id": c.id,
            "action": c.action,
            "reason": c.reason,
            "duration_minutes": c.duration_minutes,
            "source": c.source,
            "moderator_id": str(c.moderator_id) if c.moderator_id else None,
            "moderator_name": users.get(c.moderator_id, {}).get("username")
            if c.moderator_id
            else None,
            "created_at": c.created_at.isoformat(),
        }
        for c in cases
    ]


@router.delete("/warns/{user_id}")
async def clear_warns(
    user_id: int,
    guild_id: int = Depends(require_moderate_members),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Сброс всех варнов участника. Чистая БД — в Discord ничего не меняется."""
    cleared = await container.clear_warns.execute(user_id, guild_id)
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "warns.clear",
        target=user_id,
        result=f"снято {cleared}",
    )
    return {"cleared": cleared}


# --- кросс-серверные баны (только чтение, только для админа панели) ----------


@router.get("/crossban")
async def crossban_flagged(
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Отмеченные участники: сидят на этом сервере, но забанены на >= порога
    ДРУГИХ серверах бота. Наружу в Discord не идёт — только панель."""
    settings = container.guild_settings
    enabled = bool(settings.current(guild_id, "banwatch_enabled"))
    threshold = int(settings.current(guild_id, "banwatch_threshold"))
    if not enabled:
        return {"enabled": False, "threshold": threshold, "flagged": []}

    candidates = await container.banwatch_flagged.execute(guild_id, threshold)
    token = container.settings.discord_token
    flagged: list[dict] = []
    for cand in candidates[:_CROSSBAN_MAX]:
        member = await fetch_guild_member(token, guild_id, cand.user_id)
        if member is None:
            continue  # забанен где-то ещё, но на этом сервере его нет — не показываем
        report = await container.banwatch_check.execute(cand.user_id, guild_id)
        flagged.append(
            {
                "user_id": str(cand.user_id),
                "name": member["name"],
                "avatar": member["avatar"],
                "count": report.count,
                "records": _records_json(report),
            }
        )
    return {"enabled": True, "threshold": threshold, "flagged": flagged}


@router.get("/crossban/{user_id}")
async def crossban_user(
    user_id: int,
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Кросс-серверная бан-история конкретного участника (поиск в панели)."""
    settings = container.guild_settings
    threshold = int(settings.current(guild_id, "banwatch_threshold"))
    # модуль выключен на этом сервере -> кросс-серверные данные не отдаём
    # (как и /crossban): не участвуешь в обмене — не запрашиваешь чужие баны
    if not bool(settings.current(guild_id, "banwatch_enabled")):
        return {
            "user_id": str(user_id),
            "username": None,
            "avatar": None,
            "count": 0,
            "threshold": threshold,
            "records": [],
            "enabled": False,
        }
    report = await container.banwatch_check.execute(user_id, guild_id)
    users = await fetch_users(container.settings.discord_token, [user_id])
    info = users.get(user_id, {})
    return {
        "user_id": str(user_id),
        "username": info.get("username"),
        "avatar": info.get("avatar"),
        "count": report.count,
        "threshold": threshold,
        "records": _records_json(report),
        "enabled": True,
    }


# --- write-действия через командный мост (реальный Discord делает бот) -------


@router.post("/ban")
async def ban(
    body: BanBody,
    guild_id: int = Depends(require_ban_members),
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
        container,
        guild_id,
        session.user_id,
        "mod.ban",
        target=body.user_id,
        details={"minutes": body.minutes, "reason": body.reason},
        result=cmd.get("status"),
    )
    return cmd


@router.post("/unban")
async def unban(
    body: UserBody,
    guild_id: int = Depends(require_ban_members),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    cmd = await run_command(
        container, guild_id, "mod.unban", {"user_id": body.user_id}, session.user_id
    )
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "mod.unban",
        target=body.user_id,
        result=cmd.get("status"),
    )
    return cmd


@router.post("/mute")
async def mute(
    body: MuteBody,
    guild_id: int = Depends(require_moderate_members),
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
        container,
        guild_id,
        session.user_id,
        "mod.mute",
        target=body.user_id,
        details={"minutes": body.minutes, "reason": body.reason},
        result=cmd.get("status"),
    )
    return cmd


@router.post("/unmute")
async def unmute(
    body: UserBody,
    guild_id: int = Depends(require_moderate_members),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    cmd = await run_command(
        container, guild_id, "mod.unmute", {"user_id": body.user_id}, session.user_id
    )
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "mod.unmute",
        target=body.user_id,
        result=cmd.get("status"),
    )
    return cmd


@router.post("/kick")
async def kick(
    body: KickBody,
    guild_id: int = Depends(require_kick_members),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    cmd = await run_command(
        container,
        guild_id,
        "mod.kick",
        {"user_id": body.user_id, "reason": body.reason},
        session.user_id,
    )
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "mod.kick",
        target=body.user_id,
        details={"reason": body.reason},
        result=cmd.get("status"),
    )
    return cmd


@router.post("/ban_permanent")
async def ban_permanent(
    body: PermBanBody,
    guild_id: int = Depends(require_ban_members),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Постоянный бан (без авторазбана). Отдельно от /ban (тот — временный)."""
    cmd = await run_command(
        container,
        guild_id,
        "mod.ban_perm",
        {"user_id": body.user_id, "reason": body.reason, "delete_days": body.delete_days},
        session.user_id,
    )
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "mod.ban_perm",
        target=body.user_id,
        details={"reason": body.reason, "delete_days": body.delete_days},
        result=cmd.get("status"),
    )
    return cmd
