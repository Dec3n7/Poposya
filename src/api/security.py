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

# Биты прав Discord (в токене — маска пользователя на конкретном сервере).
# Вход в панель по-прежнему требует MANAGE_GUILD/админ (см. discord_oauth), а
# каждое действие внутри дополнительно сверяется со своим правом.
PERM_KICK_MEMBERS = 0x2
PERM_BAN_MEMBERS = 0x4
PERM_ADMINISTRATOR = 0x8
PERM_MANAGE_GUILD = 0x20
PERM_MANAGE_ROLES = 0x10000000
PERM_MODERATE_MEMBERS = 0x10000000000


@dataclass(frozen=True)
class SessionGuild:
    id: int
    name: str
    icon: str | None
    # маска прав Discord пользователя на этом сервере. None — легаси-токен,
    # выданный до перехода на пер-действие права: трактуем как «всё можно» (старое
    # поведение), строгую проверку такой пользователь получит при следующем логине.
    permissions: int | None = None


@dataclass(frozen=True)
class Session:
    user_id: int
    username: str
    avatar: str | None
    # серверы, где пользователь может управлять настройками (MANAGE_GUILD/админ);
    # пересечение с серверами бота проверяется отдельно на бэкенде
    guilds: list[SessionGuild]
    # эпоха сессии пользователя из токена (claim `ep`); сверяется с серверной для
    # отзыва (real logout / операторский отзыв). Легаси-токен без claim = 0.
    epoch: int = 0
    # момент НАЧАЛА сессии (unix, claim `iat0`) — переносится при скользящем
    # продлении, чтобы держать абсолютный потолок жизни сессии независимо от
    # активности. Легаси-токен без claim = 0 (потолок отсчитается заново).
    issued_at: int = 0

    def can_manage(self, guild_id: int) -> bool:
        return any(g.id == guild_id for g in self.guilds)

    def has_permission(self, guild_id: int, bit: int) -> bool:
        """Есть ли у пользователя конкретное право Discord на сервере. ADMINISTRATOR
        покрывает всё. Токен без записанных битов (permissions=None) трактуется
        fail-closed — прав НЕТ: свежий токен всегда несёт маску прав, а None
        бывает лишь у легаси-токенов, которые серверный рубильник версии сессий
        (web_session_version) и так гасит. Раньше None означал «всё можно» — это
        была дыра (протухший легаси-токен = полный доступ)."""
        for g in self.guilds:
            if g.id == guild_id:
                if g.permissions is None:
                    return False
                return bool(g.permissions & (PERM_ADMINISTRATOR | bit))
        return False


def encode_session(
    secret: str,
    session: Session,
    ttl_hours: int,
    version: int = 1,
    epoch: int = 0,
    *,
    idle_minutes: int = 0,
    session_start: int | None = None,
) -> str:
    """Подписать сессию.

    `ttl_hours` — АБСОЛЮТНЫЙ потолок жизни сессии от её начала (`session_start`,
    он же claim `iat0`; None = сейчас). `idle_minutes` > 0 включает скользящее
    окно бездействия: exp = min(now + idle, начало + потолок). При каждом запросе
    токен перевыпускается (см. current_session) — так простой дольше idle_minutes
    выкидывает пользователя на стороне СЕРВЕРА (истёкший exp), а не только гасит
    вкладку в JS. idle_minutes = 0 — прежнее поведение (exp = now + ttl_hours)."""
    now = int(time.time())
    start = session_start if session_start is not None else now
    absolute_deadline = start + ttl_hours * 3600
    if idle_minutes > 0:
        exp = min(now + idle_minutes * 60, absolute_deadline)
    else:
        exp = absolute_deadline
    payload = {
        "sub": str(session.user_id),
        "username": session.username,
        "avatar": session.avatar,
        "guilds": [
            {
                "id": str(g.id),
                "name": g.name,
                "icon": g.icon,
                # perms как строка (маска до 2^40+); отсутствие ключа = легаси-токен
                **({"perms": str(g.permissions)} if g.permissions is not None else {}),
            }
            for g in session.guilds
        ],
        # sv — глобальная версия сессий (аварийный logout всех, web_session_version)
        "sv": version,
        # ep — эпоха сессий ЭТОГО пользователя (индивидуальный отзыв/real logout)
        "ep": epoch,
        # iat0 — начало сессии (для абсолютного потолка при скользящем продлении)
        "iat0": start,
        "iat": now,
        "exp": exp,
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
            SessionGuild(
                id=int(g["id"]),
                name=g["name"],
                icon=g.get("icon"),
                # нет ключа perms -> легаси-токен (permissions=None, «всё можно»)
                permissions=(int(g["perms"]) if g.get("perms") is not None else None),
            )
            for g in payload.get("guilds", [])
        ]
        return Session(
            user_id=int(payload["sub"]),
            username=payload["username"],
            avatar=payload.get("avatar"),
            guilds=guilds,
            # нет claim `ep` (легаси-токен) = эпоха 0
            epoch=int(payload.get("ep", 0)),
            # нет claim `iat0` (легаси-токен) = отсчитываем потолок от iat/now
            issued_at=int(payload.get("iat0", payload.get("iat", 0))),
        )
    except (KeyError, ValueError, TypeError):
        return None
