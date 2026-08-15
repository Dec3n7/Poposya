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
from src.api.dependencies import current_session, get_container, require_operator
from src.api.discord_users import avatar_url, guild_icon_url
from src.api.schemas import GuildDTO, GuildPermsDTO, MeDTO
from src.api.security import (
    CSRF_COOKIE,
    OAUTH_STATE_COOKIE,
    PERM_BAN_MEMBERS,
    PERM_KICK_MEMBERS,
    PERM_MANAGE_ROLES,
    PERM_MODERATE_MEMBERS,
    SESSION_COOKIE,
    Session,
    SessionGuild,
    csrf_token,
    decode_session,
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


def _cookie_max_age(settings) -> int:
    """Срок жизни куки сессии в секундах: окно бездействия при включённом idle
    (кука обновляется на каждом запросе), иначе — абсолютный TTL."""
    if settings.web_idle_ttl_minutes > 0:
        return settings.web_idle_ttl_minutes * 60
    return settings.web_session_ttl_hours * 3600


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
    epoch = container.session_epochs.epoch_of(session.user_id)
    jwt_token = encode_session(
        s.web_session_secret,
        session,
        s.web_session_ttl_hours,
        s.web_session_version,
        epoch,
        idle_minutes=s.web_idle_ttl_minutes,
    )
    resp = RedirectResponse(s.web_allowed_origin)
    resp.delete_cookie(OAUTH_STATE_COOKIE)
    resp.set_cookie(
        SESSION_COOKIE,
        jwt_token,
        # кука живёт окно бездействия (при включённом idle) — тогда её сбросит и
        # сам браузер; сервер всё равно держит границу через exp в JWT
        max_age=_cookie_max_age(s),
        httponly=True,
        samesite="lax",
        secure=_secure_cookies(container),
    )
    # CSRF double-submit: НЕ-httpOnly кука с токеном сессии — фронт читает её и
    # шлёт эхом в X-CSRF-Token на мутациях (сверяет csrf_origin_guard)
    resp.set_cookie(
        CSRF_COOKIE,
        csrf_token(s.web_session_secret, session.user_id, epoch),
        max_age=_cookie_max_age(s),
        httponly=False,
        samesite="lax",
        secure=_secure_cookies(container),
    )
    return resp


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, container: ApiContainer = Depends(get_container)) -> Response:
    # серверный отзыв: бампим эпоху пользователя, чтобы токен (в т.ч. скопированный
    # из этой куки) стал недействителен, а не только «забываем» куку в браузере
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        s = container.settings
        session = decode_session(s.web_session_secret, token, s.web_session_version)
        if session is not None:
            await container.session_epochs.bump(session.user_id)
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    resp.delete_cookie(SESSION_COOKIE, samesite="lax", secure=_secure_cookies(container))
    return resp


@router.post("/revoke/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_user(
    user_id: int,
    _operator: Session = Depends(require_operator),
    container: ApiContainer = Depends(get_container),
) -> Response:
    """Оператор бота отзывает ВСЕ веб-сессии пользователя (разжалованный админ,
    утёкший токен). Гейт — только оператор (web_operator_ids)."""
    await container.session_epochs.bump(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
