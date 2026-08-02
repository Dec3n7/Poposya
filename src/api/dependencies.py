"""FastAPI-зависимости: доступ к контейнеру и текущей сессии.

Проверки прав — только тут (бэкенд). Фронт никогда не решает, кто владелец.
"""

from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request, status

from src.api.container import ApiContainer
from src.api.discord_oauth import OAuthError
from src.api.security import (
    PERM_BAN_MEMBERS,
    PERM_KICK_MEMBERS,
    PERM_MANAGE_ROLES,
    PERM_MODERATE_MEMBERS,
    SESSION_COOKIE,
    Session,
    decode_session,
)


def get_container(request: Request) -> ApiContainer:
    return request.app.state.container


def current_session(request: Request) -> Session:
    """Расшифровать сессию из куки. 401, если её нет или подпись невалидна."""
    container = get_container(request)
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "не авторизован")
    session = decode_session(
        container.settings.web_session_secret, token, container.settings.web_session_version
    )
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "сессия недействительна")
    return session


def require_operator(
    request: Request,
    session: Session = Depends(current_session),
) -> Session:
    """Доступ к управлению персонами — только оператор(ы) бота из
    web_operator_ids (владелец). Серверные админы персону не трогают вовсе.
    Границу проверяет ТОЛЬКО бэкенд; под этой зависимостью будут все роуты
    персон (P2)."""
    container = get_container(request)
    if session.user_id not in container.settings.web_operator_ids:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "только оператор бота")
    return session


async def _assert_bot_present(request: Request, guild_id: int) -> None:
    """На сервере реально есть бот (иначе настраивать/модерировать нечего)."""
    container = get_container(request)
    try:
        bot_guild_ids = await container.bot_guilds.get()
    except OAuthError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "не удалось проверить серверы бота"
        ) from None
    if guild_id not in bot_guild_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "бота нет на этом сервере")


async def require_guild_manager(
    guild_id: int,
    request: Request,
    session: Session = Depends(current_session),
) -> int:
    """Доступ к серверу в панели (чтение и настройки). Оба условия — ТОЛЬКО бэкенд:
    1) пользователь может управлять этим сервером (MANAGE_GUILD/админ из OAuth);
    2) на сервере есть бот. guild_id берётся из пути `/api/guilds/{guild_id}/...`.
    Действия модерации/ролей дополнительно гейтятся по конкретному праву — см.
    require_guild_permission ниже."""
    if not session.can_manage(guild_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "нет прав управлять этим сервером")
    await _assert_bot_present(request, guild_id)
    return guild_id


def require_guild_permission(
    bit: int, label: str
) -> Callable[[int, Request, Session], Awaitable[int]]:
    """Фабрика гварда: помимо входа в панель требует конкретное право Discord на
    этом сервере (бан/кик/тайм-аут/роли). Закрывает разрыв, когда MANAGE_GUILD
    давал доступ ко ВСЕМ действиям бота, даже без соответствующего права."""

    async def dependency(
        guild_id: int,
        request: Request,
        session: Session = Depends(current_session),
    ) -> int:
        if not session.has_permission(guild_id, bit):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"на сервере нужно право Discord: {label}"
            )
        await _assert_bot_present(request, guild_id)
        return guild_id

    return dependency


async def require_any_moderator(
    guild_id: int,
    request: Request,
    session: Session = Depends(current_session),
) -> int:
    """Разбор апелляций: достаточно любого модераторского права (бан/кик/тайм-аут),
    т.к. одобрение снимает разные наказания."""
    if not (
        session.has_permission(guild_id, PERM_BAN_MEMBERS)
        or session.has_permission(guild_id, PERM_KICK_MEMBERS)
        or session.has_permission(guild_id, PERM_MODERATE_MEMBERS)
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "нужно право модерации (бан, кик или тайм-аут)"
        )
    await _assert_bot_present(request, guild_id)
    return guild_id


# готовые гварды для роутеров
require_ban_members = require_guild_permission(PERM_BAN_MEMBERS, "Банить участников")
require_kick_members = require_guild_permission(PERM_KICK_MEMBERS, "Выгонять участников")
require_moderate_members = require_guild_permission(PERM_MODERATE_MEMBERS, "Тайм-аут участникам")
require_manage_roles = require_guild_permission(PERM_MANAGE_ROLES, "Управление ролями")
