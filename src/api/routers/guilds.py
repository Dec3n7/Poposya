"""Ресурсы сервера: каналы (для пикера) и сводка-дашборд (overview)."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from src.api.audit import record_audit
from src.api.command_client import run_command
from src.api.dependencies import current_session, get_container, require_guild_manager
from src.api.discord_guild import fetch_guild_channels
from src.api.discord_oauth import OAuthError
from src.api.discord_users import fetch_users
from src.api.security import Session

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
    voice = await container.voice_leaderboard.execute(guild_id, limit=10)
    birthdays = await container.upcoming_birthdays.execute(guild_id, datetime.now(UTC).date())
    profiles = await container.list_profiles.execute(guild_id)

    # один поход в Discord за именами/аватарами для всех витрин сразу
    ids = (
        {e.user_id for e in board} | {uid for uid, _ in voice} | {uid for uid, _, _, _ in birthdays}
    )
    users = await fetch_users(container.settings.discord_token, list(ids))
    role_names = container.guild_settings.resolved(guild_id).relationship_role_names

    def role_name(idx: int | None) -> str | None:
        return role_names[idx] if idx is not None and 0 <= idx < len(role_names) else None

    def user_fields(uid: int) -> dict[str, object]:
        return {
            "user_id": str(uid),
            "username": users.get(uid, {}).get("username"),
            "avatar": users.get(uid, {}).get("avatar"),
        }

    # распределение по ролям-статусам (для пончика на «Обзоре»): считаем по всем
    # профилям, только у кого есть роль (role_index != None), по возрастанию тира
    dist: dict[int, int] = {}
    for p in profiles:
        if p.role_index is not None:
            dist[p.role_index] = dist.get(p.role_index, 0) + 1

    return {
        "leaderboard": [
            {
                **user_fields(e.user_id),
                "points": e.points,
                "role": role_name(e.role_index),
                "role_index": e.role_index,
                "is_exclusive": e.is_exclusive,
            }
            for e in board
        ],
        "distribution": [
            {"index": idx, "name": role_name(idx), "count": dist[idx]} for idx in sorted(dist)
        ],
        "counts": {
            "watchlist": len(watchlist),
            "watched": len(watched),
            "playlists": len(playlists),
        },
        "voice": [{**user_fields(uid), "hours": hours} for uid, hours in voice],
        "birthdays": [
            {**user_fields(uid), "month": month, "day": day, "in_days": in_days}
            for uid, month, day, in_days in birthdays
        ],
    }


@router.get("/summary")
async def summary(
    guild_id: int = Depends(require_guild_manager),
    container=Depends(get_container),
) -> dict[str, int]:
    """Лёгкие счётчики для бейджей на сайдбаре: активные баны, замороженные
    профили, участники с варнами. Только числа — без похода в Discord за именами,
    чтобы бейджи грузились мгновенно при выборе сервера."""
    bans = await container.list_bans.execute(guild_id, datetime.now(UTC))
    warns = await container.guild_warns.execute(guild_id)
    profiles = await container.list_profiles.execute(guild_id)
    return {
        "bans": len(bans),
        "warns_users": len(warns),
        "frozen": sum(1 for p in profiles if p.frozen),
    }


@router.get("/overview/trends")
async def overview_trends(
    days: int = Query(default=30, ge=2, le=365),
    guild_id: int = Depends(require_guild_manager),
    container=Depends(get_container),
) -> dict[str, list[list[object]]]:
    """Серии суточных снапшотов метрик за последние `days` дней — для спарклайнов
    на «Обзоре». Ключ — имя метрики, значение — [[день-ISO, число], …]. Пусто,
    пока снапшоты не накопились (например, на dev-SQLite без работающего бота)."""
    since = (datetime.now(UTC).date()) - timedelta(days=days - 1)
    series = await container.get_trends.execute(guild_id, since)
    return {
        metric: [[day.isoformat(), value] for day, value in points]
        for metric, points in series.items()
    }


@router.get("/activity")
async def activity(
    days: int = Query(default=30, ge=2, le=365),
    guild_id: int = Depends(require_guild_manager),
    container=Depends(get_container),
) -> dict[str, object]:
    """Активность сервера за `days` дней: сообщения/день (для спарклайна) и два
    хитмапа день-недели×час (7×24, UTC) — сообщения и минуты в войсе. Пусто, пока
    бот не накопил счётчики."""
    since = (datetime.now(UTC).date()) - timedelta(days=days - 1)
    stats = await container.activity_stats.execute(guild_id, since)
    return {
        "daily": [[day.isoformat(), count] for day, count in stats.daily],
        "heatmap": stats.heatmap,
        "voice": stats.voice_heatmap,
    }


@router.delete("/cinema/movies/{entry_id}")
async def remove_movie(
    entry_id: int,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container=Depends(get_container),
) -> dict:
    """Убрать фильм из вотчлиста (админ панели). Прямо в БД, без Discord-побочки."""
    status_str, entry = await container.remove_movie.execute(
        guild_id, entry_id, session.user_id, is_admin=True
    )
    if status_str == "not_found":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Фильм не найден в вотчлисте")
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "movie.remove",
        target=entry.title if entry else str(entry_id),
        result=status_str,
    )
    return {"status": status_str}


class BotProfileBody(BaseModel):
    nick: str = ""
    avatar_url: str = ""
    banner_url: str = ""
    # загруженный+обрезанный аватар как data-URL (base64); приоритетнее avatar_url
    avatar_data: str = ""
    # загруженный+обрезанный баннер как data-URL (base64); приоритетнее banner_url
    banner_data: str = ""


@router.get("/bot-profile")
async def bot_profile(
    guild_id: int = Depends(require_guild_manager),
    container=Depends(get_container),
) -> dict:
    """Сохранённый профиль бота. avatar_data отдаём целиком — панель держит его и
    пере-отправляет неизменным при правке ника (иначе загруженный аватар стёрся бы)."""
    p = await container.get_bot_profile.execute(guild_id)
    return {
        "nick": p.nick,
        "avatar_url": p.avatar_url,
        "banner_url": p.banner_url,
        "avatar_data": p.avatar_data,
        "banner_data": p.banner_data,
    }


@router.put("/bot-profile")
async def set_bot_profile(
    body: BotProfileBody,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container=Depends(get_container),
) -> dict:
    """Сохранить профиль (загруженный аватар = кэш в БД) и сразу применить к боту
    через мост (guild.me.edit). Пустое значение = сброс к глобальному."""
    await container.set_bot_profile.execute(
        guild_id,
        body.nick,
        body.avatar_url,
        body.banner_url,
        body.avatar_data,
        body.banner_data,
    )
    cmd = await run_command(
        container,
        guild_id,
        "profile.apply",
        {
            "nick": body.nick,
            "avatar_url": body.avatar_url,
            "banner_url": body.banner_url,
            "avatar_data": body.avatar_data,
            "banner_data": body.banner_data,
        },
        session.user_id,
    )
    await record_audit(
        container, guild_id, session.user_id, "profile.apply", result=cmd.get("status")
    )
    return {"command": cmd}


@router.get("/audit")
async def audit(
    limit: int = Query(default=100, ge=1, le=500),
    guild_id: int = Depends(require_guild_manager),
    container=Depends(get_container),
) -> list[dict]:
    """Журнал действий панели: кто/что/над кем/результат/когда. Имена акторов и
    целей-пользователей (числовые target) резолвим одним походом в Discord."""
    entries = await container.list_audit.execute(guild_id, limit)
    ids = {e.actor_id for e in entries}
    for e in entries:
        if e.target and e.target.isdigit():
            ids.add(int(e.target))
    users = await fetch_users(container.settings.discord_token, list(ids))
    return [
        {
            "id": e.id,
            "actor_id": str(e.actor_id),
            "actor_name": users.get(e.actor_id, {}).get("username"),
            "actor_avatar": users.get(e.actor_id, {}).get("avatar"),
            "action": e.action,
            "target": e.target,
            "target_name": (
                users.get(int(e.target), {}).get("username")
                if e.target and e.target.isdigit()
                else None
            ),
            "details": e.details,
            "result": e.result,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]


@router.get("/cinema")
async def cinema(
    guild_id: int = Depends(require_guild_manager),
    container=Depends(get_container),
) -> dict[str, object]:
    """Киноклуб: вотчлист (с голосами) и золотой фонд (просмотренные с оценками).
    Те же read-use-case'ы, что и у бота."""
    watchlist = await container.list_watchlist.execute(guild_id)  # [(entry, up, down)]
    watched = await container.top_watched.execute(guild_id)  # [entry], по среднему баллу

    return {
        "watchlist": [
            {"id": e.id, "title": e.title, "year": e.year, "up": up, "down": down}
            for e, up, down in watchlist
        ],
        "watched": [
            {
                "id": e.id,
                "title": e.title,
                "year": e.year,
                "avg_score": e.avg_score,
                "ratings_count": e.ratings_count,
                "poposya_score": e.poposya_score,
                "poposya_review": e.poposya_review,
            }
            for e in watched
        ],
    }


@router.get("/cinema/movies/{entry_id}")
async def movie_ratings(
    entry_id: int,
    guild_id: int = Depends(require_guild_manager),
    container=Depends(get_container),
) -> dict[str, object]:
    """Все оценки и рецензии по фильму — для раскрытия карточки на панели.
    Оценивших резолвим в имена/аватары; порядок — по времени оценки."""
    ratings = await container.movie_ratings.execute(entry_id)
    users = await fetch_users(container.settings.discord_token, [r.user_id for r in ratings])
    return {
        "ratings": [
            {
                "user_id": str(r.user_id),
                "username": users.get(r.user_id, {}).get("username"),
                "avatar": users.get(r.user_id, {}).get("avatar"),
                "score": r.score,
                "review": r.text,
            }
            for r in ratings
        ],
    }
