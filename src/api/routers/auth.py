"""Авторизация через Discord OAuth2.

Поток: /login (редирект в Discord) -> /callback (обмен кода, ставим JWT-сессию,
редирект на фронт) -> /me (кто я + мои управляемые серверы) -> /logout.
"""

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from src.api import discord_oauth
from src.api.container import ApiContainer
from src.api.dependencies import current_session, get_container
from src.api.discord_users import avatar_url, guild_icon_url
from src.api.schemas import GuildDTO, GuildPermsDTO, MeDTO
from src.api.security import (
    OAUTH_STATE_COOKIE,
    PERM_BAN_MEMBERS,
    PERM_KICK_MEMBERS,
    PERM_MANAGE_ROLES,
    PERM_MODERATE_MEMBERS,
    SESSION_COOKIE,
    Session,
    SessionGuild,
    encode_session,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _guild_perms(session: Session, g: SessionGuild) -> GuildPermsDTO:
    """Булевы права для фронта — считает бэкенд, фронт по ним только гасит кнопки.
    ADMINISTRATOR/легаси-токен покрывают всё через Session.has_permission."""
    return GuildPermsDTO(
        can_ban=session.has_permission(g.id, PERM_BAN_MEMBERS),
        can_kick=session.has_permission(g.id, PERM_KICK_MEMBERS),
        can_moderate=session.has_permission(g.id, PERM_MODERATE_MEMBERS),
        can_manage_roles=session.has_permission(g.id, PERM_MANAGE_ROLES),
    )


def _secure_cookies(container: ApiContainer) -> bool:
    # на проде redirect по https -> куки Secure; на localhost (http) — нет,
    # иначе браузер их отбросит
    return container.settings.web_oauth_redirect.startswith("https")


@router.get("/login")
async def login(container: ApiContainer = Depends(get_container)) -> RedirectResponse:
    s = container.settings
    if not s.discord_client_id or not s.web_session_secret:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "OAuth не настроен (.env)")
    state = secrets.token_urlsafe(24)  # CSRF-защита: сверим на callback
    url = discord_oauth.authorize_url(s.discord_client_id, s.web_oauth_redirect, state)
    resp = RedirectResponse(url)
    resp.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=_secure_cookies(container),
    )
    return resp


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    container: ApiContainer = Depends(get_container),
) -> RedirectResponse:
    s = container.settings
    saved_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not code or not state or not saved_state or state != saved_state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "неверный state или код")
    try:
        token = await discord_oauth.exchange_code(
            s.discord_client_id, s.discord_client_secret, code, s.web_oauth_redirect
        )
        user, guilds = await discord_oauth.fetch_identity(token)
    except discord_oauth.OAuthError:
        logger.warning("OAuth callback: Discord отказал", exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Discord OAuth не удался") from None

    session = Session(
        user_id=int(user["id"]),
        username=user.get("username", ""),
        avatar=user.get("avatar"),
        guilds=discord_oauth.manageable_guilds(guilds),
    )
    jwt_token = encode_session(
        s.web_session_secret, session, s.web_session_ttl_hours, s.web_session_version
    )
    resp = RedirectResponse(s.web_allowed_origin)
    resp.delete_cookie(OAUTH_STATE_COOKIE)
    resp.set_cookie(
        SESSION_COOKIE,
        jwt_token,
        max_age=s.web_session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=_secure_cookies(container),
    )
    return resp


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(container: ApiContainer = Depends(get_container)) -> Response:
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    resp.delete_cookie(SESSION_COOKIE, samesite="lax", secure=_secure_cookies(container))
    return resp


@router.get("/me", response_model=MeDTO)
async def me(
    session: Session = Depends(current_session),
    container: ApiContainer = Depends(get_container),
) -> MeDTO:
    # показываем только серверы, где реально есть бот (иначе настраивать нечего).
    # Если Discord недоступен — не блокируем вход, отдаём все управляемые.
    try:
        bot_ids: set[int] | None = await container.bot_guilds.get()
    except discord_oauth.OAuthError:
        bot_ids = None
    guilds = [g for g in session.guilds if bot_ids is None or g.id in bot_ids]
    return MeDTO(
        user_id=str(session.user_id),
        username=session.username,
        avatar=avatar_url(session.user_id, session.avatar),
        guilds=[
            GuildDTO(
                id=str(g.id),
                name=g.name,
                icon=guild_icon_url(g.id, g.icon),
                perms=_guild_perms(session, g),
            )
            for g in guilds
        ],
        is_operator=session.user_id in container.settings.web_operator_ids,
    )
