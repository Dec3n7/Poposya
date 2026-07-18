"""Ресурсы сервера: каналы (для пикера) и сводка-дашборд (overview)."""

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import get_container, require_guild_manager
from src.api.discord_guild import fetch_guild_channels
from src.api.discord_oauth import OAuthError
from src.api.discord_users import fetch_users

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


@router.get("/overview")
async def overview(
    guild_id: int = Depends(require_guild_manager),
    container=Depends(get_container),
) -> dict[str, object]:
    """Сводка сервера: топ по очкам (с именами/аватарами) + счётчики. Всё через
    те же read-use-case'ы, что у бота."""
    board = await container.leaderboard.execute(guild_id, limit=10)
    watchlist = await container.list_watchlist.execute(guild_id)
    watched = await container.top_watched.execute(guild_id)
    playlists = await container.list_playlists.execute(guild_id)

    users = await fetch_users(container.settings.discord_token, [e.user_id for e in board])
    role_names = container.guild_settings.resolved(guild_id).relationship_role_names

    def role_name(idx: int | None) -> str | None:
        return role_names[idx] if idx is not None and 0 <= idx < len(role_names) else None

    return {
        "leaderboard": [
            {
                "user_id": str(e.user_id),
                "username": users.get(e.user_id, {}).get("username"),
                "avatar": users.get(e.user_id, {}).get("avatar"),
                "points": e.points,
                "role": role_name(e.role_index),
                "is_exclusive": e.is_exclusive,
            }
            for e in board
        ],
        "counts": {
            "watchlist": len(watchlist),
            "watched": len(watched),
            "playlists": len(playlists),
        },
    }
