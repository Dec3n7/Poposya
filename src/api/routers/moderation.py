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

from src.api.container import ApiContainer
from src.api.dependencies import get_container, require_guild_manager
from src.api.discord_users import fetch_users

router = APIRouter(prefix="/api/guilds/{guild_id}/moderation", tags=["moderation"])


def _now() -> datetime:
    return datetime.now(UTC)


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
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Сброс всех варнов участника. Чистая БД — в Discord ничего не меняется."""
    cleared = await container.clear_warns.execute(user_id, guild_id)
    return {"cleared": cleared}
