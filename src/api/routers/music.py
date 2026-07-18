"""Музыка: read-модуль плейлистов сервера.

Только чтение — сами плейлисты создаются/играются в Discord. Live-очередь и
«сейчас играет» здесь НЕ отдаём: состояние плеера живёт в памяти бота, для него
нужен командный мост панель↔бот (отдельная фаза).
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.api.command_client import run_command
from src.api.container import ApiContainer
from src.api.dependencies import current_session, get_container, require_guild_manager
from src.api.discord_users import fetch_users
from src.api.security import Session

router = APIRouter(prefix="/api/guilds/{guild_id}/music", tags=["music"])


class ControlBody(BaseModel):
    action: Literal["pause", "resume", "skip", "stop"]


@router.get("/playlists")
async def playlists(
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> list[dict]:
    """Плейлисты сервера: имя, число треков, автор (по алфавиту)."""
    rows = await container.list_playlists.execute(guild_id)  # [(name, count, created_by)]
    users = await fetch_users(container.settings.discord_token, [r[2] for r in rows])
    return [
        {
            "name": name,
            "track_count": count,
            "author_id": str(created_by),
            "author_name": users.get(created_by, {}).get("username"),
        }
        for name, count, created_by in rows
    ]


@router.get("/playlists/{name}")
async def playlist_tracks(
    name: str,
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Треки конкретного плейлиста (для раскрытия карточки)."""
    tracks = await container.load_playlist.execute(guild_id, name, requested_by=0)
    if tracks is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Плейлист не найден")
    return {
        "name": name,
        "tracks": [
            {
                "title": t.title,
                "uploader": t.uploader,
                "duration": t.duration,
                "thumbnail": t.thumbnail,
            }
            for t in tracks
        ],
    }


@router.post("/control")
async def control(
    body: ControlBody,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Управление живой сессией плеера через командный мост: pause/resume/
    skip/stop. Запуск нового трека невозможен — для него нужен участник в войсе."""
    return await run_command(
        container, guild_id, f"music.{body.action}", {}, session.user_id
    )
