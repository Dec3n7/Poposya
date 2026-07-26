"""JWT-сессия в httpOnly-куке. Без серверного стора (Redis не нужен): всё, что
нужно для авторизации и проверки прав, лежит в подписанном токене.

Токен подписан `web_session_secret` (HS256) — подделать нельзя, но содержимое
пользователь может прочитать (это его же данные: id, имя, его серверы). Пароли/
секреты сюда не кладём.
"""

import time
from dataclasses import dataclass

import jwt

SESSION_COOKIE = "poposya_session"
OAUTH_STATE_COOKIE = "poposya_oauth_state"
_ALGO = "HS256"


@dataclass(frozen=True)
class SessionGuild:
    id: int
    name: str
    icon: str | None


@dataclass(frozen=True)
class Session:
    user_id: int
    username: str
    avatar: str | None
    # серверы, где пользователь может управлять настройками (MANAGE_GUILD/админ);
    # пересечение с серверами бота проверяется отдельно на бэкенде
    guilds: list[SessionGuild]

    def can_manage(self, guild_id: int) -> bool:
        return any(g.id == guild_id for g in self.guilds)


def encode_session(secret: str, session: Session, ttl_hours: int, version: int = 1) -> str:
    now = int(time.time())
    payload = {
        "sub": str(session.user_id),
        "username": session.username,
        "avatar": session.avatar,
        "guilds": [{"id": str(g.id), "name": g.name, "icon": g.icon} for g in session.guilds],
        # sv — версия сессии для серверного отзыва (см. web_session_version)
        "sv": version,
        "iat": now,
        "exp": now + ttl_hours * 3600,
    }
    return jwt.encode(payload, secret, algorithm=_ALGO)


def decode_session(secret: str, token: str, version: int = 1) -> Session | None:
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None
    # Серверный отзыв: токены со старой версией сессии больше не действительны.
    # Отсутствие claim (легаси-токен) трактуем как версию 1 — не гасим зря при
    # первом деплое рубильника.
    try:
        if int(payload.get("sv", 1)) != version:
            return None
    except (TypeError, ValueError):
        return None
    try:
        guilds = [
            SessionGuild(id=int(g["id"]), name=g["name"], icon=g.get("icon"))
            for g in payload.get("guilds", [])
        ]
        return Session(
            user_id=int(payload["sub"]),
            username=payload["username"],
            avatar=payload.get("avatar"),
            guilds=guilds,
        )
    except (KeyError, ValueError, TypeError):
        return None
