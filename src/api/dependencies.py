"""FastAPI-зависимости: доступ к контейнеру и текущей сессии.

Проверки прав — только тут (бэкенд). Фронт никогда не решает, кто владелец.
"""

import time
from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request, Response, status

from src.api.container import ApiContainer
from src.api.discord_oauth import OAuthError
from src.api.security import (
    CSRF_COOKIE,
    PERM_BAN_MEMBERS,
    PERM_KICK_MEMBERS,
    PERM_MANAGE_ROLES,
    PERM_MODERATE_MEMBERS,
    SESSION_COOKIE,
    Session,
    csrf_token,
    decode_session,
    encode_session,
)
from src.application.interfaces.entitlements import PlanTier

# методы без побочных эффектов не гейтим свежестью прав (чтения всегда проходят)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def get_container(request: Request) -> ApiContainer:
    return request.app.state.container


def _set_csrf_cookie(container: ApiContainer, session: Session, response: Response) -> None:
    """Выдать/обновить НЕ-httpOnly куку с CSRF-токеном сессии (её читает фронт и
    шлёт эхом в заголовке). Ставится на каждом авторизованном запросе, чтобы и
    уже вошедшие пользователи получили токен без перелогина."""
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token(container.settings.web_session_secret, session.user_id, session.epoch),
        max_age=_cookie_ttl_seconds(container),
        httponly=False,  # фронту нужно прочитать значение из JS
        samesite="lax",
        secure=container.settings.web_oauth_redirect.startswith("https"),
    )


def _cookie_ttl_seconds(container: ApiContainer) -> int:
    s = container.settings
    if s.web_idle_ttl_minutes > 0:
        return s.web_idle_ttl_minutes * 60
    return s.web_session_ttl_hours * 3600


def assert_perms_fresh(request: Request, container: ApiContainer, session: Session) -> None:
    """Привилегированные ДЕЙСТВИЯ требуют не слишком старого снимка прав Discord
    (web_perm_ttl_minutes). Чтения (safe-методы) и выключенный потолок (0) —
    проходят. Иначе 401: фронт по любому 401 уводит на логин, где права
    перечитываются из OAuth. Так разжалованный в Discord админ теряет доступ к
    действиям за минуты, а не за весь TTL сессии."""
    ttl = container.settings.web_perm_ttl_minutes
    if ttl <= 0 or request.method in _SAFE_METHODS:
        return
    if int(time.time()) - session.perms_at > ttl * 60:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "права устарели — войдите заново")


def _slide_session(container: ApiContainer, session: Session, response: Response) -> None:
    """Скользящее продление: на каждом запросе перевыпускаем куку с новым окном
    бездействия, не сдвигая абсолютный потолок (issued_at). Так простой дольше
    web_idle_ttl_minutes истекает на сервере (exp в JWT), а не только в браузере.
    У абсолютного потолка не продлеваем — пусть истечёт естественно."""
    s = container.settings
    if s.web_idle_ttl_minutes <= 0:
        return
    remaining_absolute = session.issued_at + s.web_session_ttl_hours * 3600 - int(time.time())
    if remaining_absolute <= 0:
        return  # достигнут абсолютный потолок — не продлеваем, дадим истечь
    window = min(s.web_idle_ttl_minutes * 60, remaining_absolute)
    fresh = encode_session(
        s.web_session_secret,
        session,
        s.web_session_ttl_hours,
        s.web_session_version,
        session.epoch,
        idle_minutes=s.web_idle_ttl_minutes,
        session_start=session.issued_at,
        # снимок прав НЕ омолаживаем при продлении — иначе web_perm_ttl_minutes
        # никогда не сработает у активного пользователя
        perms_at=session.perms_at,
    )
    response.set_cookie(
        SESSION_COOKIE,
        fresh,
        max_age=window,
        httponly=True,
        samesite="lax",
        secure=s.web_oauth_redirect.startswith("https"),
    )


def current_session(request: Request, response: Response) -> Session:
    """Расшифровать сессию из куки. 401, если её нет или подпись невалидна.
    Заодно скользяще продлевает окно бездействия (web_idle_ttl_minutes)."""
    container = get_container(request)
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "не авторизован")
    session = decode_session(
        container.settings.web_session_secret, token, container.settings.web_session_version
    )
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "сессия недействительна")
    # серверный отзыв: эпоха в токене устарела (real logout / операторский отзыв)
    if session.epoch != container.session_epochs.epoch_of(session.user_id):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "сессия отозвана")
    _slide_session(container, session, response)
    # double-submit: выдаём/обновляем куку CSRF-токена (её эхом сверяет middleware)
    _set_csrf_cookie(container, session, response)
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


def assert_guild_premium(container: ApiContainer, guild_id: int) -> None:
    """Функция только для серверов с активной подпиской: эффективный тариф ≥
    Premium. При выключенном enforcement (default=pro) проходят все — консистентно
    с «все получают максимум»; при default=free — только сервер с активной
    Premium/Pro-подпиской."""
    tier, _expires, _active = container.entitlements.current(guild_id)
    if tier < PlanTier.PREMIUM:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED, "нужна активная подписка сервера (Premium)"
        )


async def require_persona_editor(
    persona_id: int,
    request: Request,
    session: Session = Depends(current_session),
) -> Session:
    """Право править персону в панели. Оператор — любую. Менеджер сервера — только
    СВОЮ заявку: персона с owner_guild_id этого сервера и статусом НЕ approved (то
    есть не назначенную серверу → бот её не видит) и только при активной подписке.
    Так админ редактирует свой черновик, но не живую персону и не чужую."""
    container = get_container(request)
    if session.user_id in container.settings.web_operator_ids:
        return session
    persona = container.persona.get(persona_id)
    if persona is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "персона не найдена")
    owner = persona.owner_guild_id
    if owner is None or persona.status == "approved":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "только оператор бота")
    if not session.can_manage(owner):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "нет прав на этот сервер")
    assert_perms_fresh(request, container, session)
    await _assert_bot_present(request, owner)
    assert_guild_premium(container, owner)
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
    assert_perms_fresh(request, get_container(request), session)
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
        assert_perms_fresh(request, get_container(request), session)
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
    assert_perms_fresh(request, get_container(request), session)
    await _assert_bot_present(request, guild_id)
    return guild_id


# готовые гварды для роутеров
require_ban_members = require_guild_permission(PERM_BAN_MEMBERS, "Банить участников")
require_kick_members = require_guild_permission(PERM_KICK_MEMBERS, "Выгонять участников")
require_moderate_members = require_guild_permission(PERM_MODERATE_MEMBERS, "Тайм-аут участникам")
require_manage_roles = require_guild_permission(PERM_MANAGE_ROLES, "Управление ролями")
