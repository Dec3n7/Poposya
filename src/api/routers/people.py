"""Люди / отношения: лидерборд, карточка человека и admin-действия.

Read + write через те же use-case'ы, что у бота (GetRank, SetPoints,
ToggleFreeze). Приватные заметки Поповы и анкету НЕ отдаём — только «публичные»
показатели (очки, роль, заморозка, ДР, глубокие диалоги). Правку очков и
заморозку бот видит сразу из БД; конкурентность закрыта локом профиля (Фаза 1).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.container import ApiContainer
from src.api.dependencies import get_container, require_guild_manager
from src.api.discord_members import fetch_guild_members
from src.api.discord_users import fetch_users

router = APIRouter(prefix="/api/guilds/{guild_id}/people", tags=["people"])


class PointsUpdate(BaseModel):
    value: int


def _role_name(container: ApiContainer, guild_id: int, idx: int | None) -> str | None:
    names = container.guild_settings.resolved(guild_id).relationship_role_names
    return names[idx] if idx is not None and 0 <= idx < len(names) else None


def _detail(container: ApiContainer, guild_id: int, user_id: int, rank, user: dict | None) -> dict:
    return {
        "user_id": str(user_id),
        "username": (user or {}).get("username"),
        "avatar": (user or {}).get("avatar"),
        "points": rank.points,
        "level": rank.level,
        "role": _role_name(container, guild_id, rank.role_index),
        "is_exclusive": rank.is_exclusive,
        "frozen": rank.frozen,
        "next_threshold": rank.next_threshold,
        "deep_dialogs": rank.deep_dialogs,
        "birthday_day": rank.birthday_day,
        "birthday_month": rank.birthday_month,
        "last_dialog_at": rank.last_dialog_at.isoformat() if rank.last_dialog_at else None,
    }


@router.get("")
async def people(
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> list[dict]:
    """Все участники сервера (через бот-токен), смерженные с профилями Попоси.
    У кого есть профиль — с очками/ролью/заморозкой; остальные — «чистые».
    Фильтры/сортировку делает фронт по этому полному списку."""
    members = await fetch_guild_members(container.settings.discord_token, guild_id)
    profiles = await container.list_profiles.execute(guild_id)
    by_id = {p.user_id: p for p in profiles}
    result = [
        {
            "user_id": str(m["user_id"]),
            "username": m["name"],
            "avatar": m["avatar"],
            "points": p.points if p else 0,
            "role": _role_name(container, guild_id, p.role_index) if p else None,
            "is_exclusive": p.is_exclusive if p else False,
            "frozen": p.frozen if p else False,
            "has_profile": p is not None,
            "last_dialog_at": (
                p.last_dialog_at.isoformat() if p and p.last_dialog_at else None
            ),
        }
        for m in members
        for p in [by_id.get(m["user_id"])]
    ]
    result.sort(key=lambda x: (-x["points"], (x["username"] or "").lower()))
    return result


@router.get("/{user_id}")
async def person(
    user_id: int,
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> dict:
    rank = await container.get_rank.execute(user_id, guild_id)
    users = await fetch_users(container.settings.discord_token, [user_id])
    return _detail(container, guild_id, user_id, rank, users.get(user_id))


@router.put("/{user_id}/points")
async def set_points(
    user_id: int,
    body: PointsUpdate,
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> dict:
    await container.set_points.execute(user_id, guild_id, body.value)
    rank = await container.get_rank.execute(user_id, guild_id)
    users = await fetch_users(container.settings.discord_token, [user_id])
    return _detail(container, guild_id, user_id, rank, users.get(user_id))


@router.post("/{user_id}/freeze")
async def toggle_freeze(
    user_id: int,
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> dict:
    """Тоглит заморозку (не начисляем очки замороженному). Возвращает новое состояние."""
    frozen = await container.toggle_freeze.execute(user_id, guild_id)
    return {"frozen": frozen}
