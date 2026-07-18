"""FastAPI-зависимости: доступ к контейнеру и текущей сессии.

Проверки прав — только тут (бэкенд). Фронт никогда не решает, кто владелец.
"""

from fastapi import HTTPException, Request, status

from src.api.container import ApiContainer
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
