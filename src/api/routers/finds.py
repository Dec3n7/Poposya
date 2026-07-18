"""Ночные находки: server-level read-обзор.

Показываем то, что осмысленно на уровне сервера: активную находку (одна за раз)
и топ коллекционеров (у кого больше всего предметов, сколько подарено Попосе).
Личные коллекции приватны — их здесь не отдаём.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from src.api.container import ApiContainer
from src.api.dependencies import get_container, require_guild_manager
from src.api.discord_users import fetch_users
from src.domain.finds.catalog import RARITY_EMOJI, RARITY_LABELS

router = APIRouter(prefix="/api/guilds/{guild_id}/finds", tags=["finds"])


def _now() -> datetime:
    return datetime.now(UTC)


@router.get("/overview")
async def overview(
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Активная находка + топ коллекционеров сервера."""
    view = await container.active_find.execute(guild_id, _now())
    collectors = await container.top_collectors.execute(guild_id, limit=20)
    users = await fetch_users(container.settings.discord_token, [c.user_id for c in collectors])

    active = None
    if view is not None:
        active = {
            "location": view.location.name,
            "location_flavor": view.location.flavor,
            "item_emoji": view.item.emoji,
            "item_name": view.item.name,
            "rarity": RARITY_LABELS[view.item.rarity],
            "rarity_emoji": RARITY_EMOJI[view.item.rarity],
            "expires_at": view.find.expires_at.isoformat(),
        }

    return {
        "active": active,
        "collectors": [
            {
                "user_id": str(c.user_id),
                "username": users.get(c.user_id, {}).get("username"),
                "avatar": users.get(c.user_id, {}).get("avatar"),
                "total": c.total,
                "gifted": c.gifted,
            }
            for c in collectors
        ],
    }
