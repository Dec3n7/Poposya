"""FastAPI-зависимости: доступ к контейнеру и текущей сессии.

Проверки прав — только тут (бэкенд). Фронт никогда не решает, кто владелец.
"""

from fastapi import Depends, HTTPException, Request, status

from src.api.container import ApiContainer
from src.api.discord_oauth import OAuthError
from src.api.security import SESSION_COOKIE, Session, decode_session


def get_container(request: Request) -> ApiContainer:
    return request.app.state.container


def current_session(request: Request) -> Session:
    """Расшифровать сессию из куки. 401, если её нет или подпись невалидна."""
    container = get_container(request)
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "не авторизован")
    session = decode_session(container.settings.web_session_secret, token)
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


async def require_guild_manager(
    guild_id: int,
    request: Request,
    session: Session = Depends(current_session),
) -> int:
    """Доступ к настройкам сервера. Оба условия проверяет ТОЛЬКО бэкенд:
    1) у пользователя есть право управлять этим сервером (из его OAuth-серверов);
    2) на сервере реально есть бот (иначе настраивать нечего).
    guild_id берётся из пути `/api/guilds/{guild_id}/...`."""
    if not session.can_manage(guild_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "нет прав управлять этим сервером")
    container = get_container(request)
    try:
        bot_guild_ids = await container.bot_guilds.get()
    except OAuthError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "не удалось проверить серверы бота"
        ) from None
    if guild_id not in bot_guild_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "бота нет на этом сервере")
    return guild_id
