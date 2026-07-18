"""Ресурсы сервера, не относящиеся к настройкам напрямую (каналы для пикера)."""

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import get_container, require_guild_manager
from src.api.discord_guild import fetch_guild_channels
from src.api.discord_oauth import OAuthError

router = APIRouter(prefix="/api/guilds/{guild_id}", tags=["guilds"])


@router.get("/channels")
async def channels(
    guild_id: int = Depends(require_guild_manager),
    container=Depends(get_container),
) -> list[dict]:
    """Каналы сервера (для выбора канала в настройках)."""
    try:
        return await fetch_guild_channels(container.settings.discord_token, guild_id)
    except OAuthError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "не удалось получить каналы") from None
