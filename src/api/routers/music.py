"""Музыка: read-модуль плейлистов сервера.

Только чтение — сами плейлисты создаются/играются в Discord. Live-очередь и
«сейчас играет» здесь НЕ отдаём: состояние плеера живёт в памяти бота, для него
нужен командный мост панель↔бот (отдельная фаза).
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.audit import record_audit
from src.api.command_client import run_command
from src.api.container import ApiContainer
from src.api.dependencies import current_session, get_container, require_guild_manager
from src.api.discord_users import fetch_users
from src.api.security import Session

router = APIRouter(prefix="/api/guilds/{guild_id}/music", tags=["music"])


class ControlBody(BaseModel):
    # действия без параметров, работают с текущей сессией плеера
    action: Literal["pause", "resume", "skip", "stop", "previous", "shuffle", "repeat"]


class VolumeBody(BaseModel):
    # доля 0..1 в UI = 0..100%; плеер сам клампит до 0..2 (200%)
    volume: float = Field(ge=0.0, le=2.0)


class SeekBody(BaseModel):
    position: int = Field(ge=0)  # секунды от начала трека


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


@router.delete("/playlists/{name}")
async def delete_playlist(
    name: str,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Удалить плейлист сервера (админ панели). Прямо в БД."""
    result = await container.delete_playlist.execute(
        guild_id, name, requester_id=session.user_id, is_admin=True
    )
    if result == "not_found":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Плейлист не найден")
    await record_audit(
        container, guild_id, session.user_id, "playlist.delete", target=name, result=result
    )
    return {"status": result}


@router.get("/now")
async def now_playing(
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> dict | None:
    """Живое состояние плеера (снапшот от бота) или null, если не играет.
    Позиция + position_at: фронт сам тикает прогресс между опросами."""
    state = await container.now_playing.execute(guild_id)
    if state is None or not state.is_active or state.current is None:
        return None

    ids = {state.current.requested_by} | {t.requested_by for t in state.queue}
    users = await fetch_users(container.settings.discord_token, list(ids))

    def track(t) -> dict:
        return {
            "title": t.title,
            "url": t.url,
            "duration": t.duration,
            "uploader": t.uploader,
            "thumbnail": t.thumbnail,
            "requested_by": str(t.requested_by),
            "requested_name": users.get(t.requested_by, {}).get("username"),
        }

    return {
        "current": track(state.current),
        "queue": [track(t) for t in state.queue],
        "position_seconds": state.position_seconds,
        "position_at": state.position_at.isoformat() if state.position_at else None,
        "is_paused": state.is_paused,
        "repeat": state.repeat,
        "volume": state.volume,
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
    cmd = await run_command(
        container, guild_id, f"music.{body.action}", {}, session.user_id
    )
    await record_audit(
        container, guild_id, session.user_id, f"music.{body.action}", result=cmd.get("status")
    )
    return cmd


@router.post("/volume")
async def set_volume(
    body: VolumeBody,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Громкость живой сессии (0..2 = 0..200%)."""
    cmd = await run_command(
        container, guild_id, "music.volume", {"volume": body.volume}, session.user_id
    )
    await record_audit(
        container, guild_id, session.user_id, "music.volume", result=cmd.get("status")
    )
    return cmd


@router.post("/seek")
async def seek(
    body: SeekBody,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Перемотка текущего трека на position секунд."""
    cmd = await run_command(
        container, guild_id, "music.seek", {"position": body.position}, session.user_id
    )
    await record_audit(
        container, guild_id, session.user_id, "music.seek", result=cmd.get("status")
    )
    return cmd


@router.delete("/queue/{position}")
async def remove_from_queue(
    position: int,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Убрать трек из очереди по 1-based номеру (как показывает /queue)."""
    cmd = await run_command(
        container, guild_id, "music.remove", {"position": position}, session.user_id
    )
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "music.remove",
        target=str(position),
        result=cmd.get("status"),
    )
    return cmd
