"""FastAPI-приложение панели: сборка, CORS, жизненный цикл, роутеры.

Права и валидация — только здесь (бэкенд), фронт лишь отображает ответы.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from src.api.container import ApiContainer
from src.api.routers import (
    appeals,
    auth,
    entitlements,
    finds,
    guilds,
    moderation,
    music,
    people,
    personas,
    premium_keys,
    roles,
    settings,
    warden,
)
from src.api.security import CSRF_HEADER, SESSION_COOKIE, csrf_token_valid, decode_session

logger = logging.getLogger(__name__)

# Холодный старт: миграции применяет БОТ, а api-процесс может подняться раньше
# и стукнуться в ещё несуществующие таблицы. Вместо crash-restart цикла ждём
# готовности схемы с бэкоффом (bot: start_period 60с). Здоровье БД проверяем
# самой рабочей загрузкой — не завязываясь на имя таблицы alembic.
_SCHEMA_WAIT_ATTEMPTS = 45
_SCHEMA_WAIT_DELAY = 2.0

# CSRF: методы без побочных эффектов не проверяем
_CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _origin_of(url: str) -> str | None:
    """scheme://host[:port] из Origin/Referer; None — если не разобрать."""
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


async def _load_state_when_ready(container: ApiContainer) -> None:
    for attempt in range(1, _SCHEMA_WAIT_ATTEMPTS + 1):
        try:
            await container.guild_settings.load_all()
            await container.persona.load_all()
            await container.session_epochs.load_all()
            await container.entitlements.load_all()
            return
        except SQLAlchemyError as exc:
            if attempt == _SCHEMA_WAIT_ATTEMPTS:
                logger.error("API: схема БД так и не готова — сдаюсь, перезапуск")
                raise
            logger.warning(
                "API: схема БД ещё не готова (попытка %d/%d, %s) — жду %.0fс",
                attempt,
                _SCHEMA_WAIT_ATTEMPTS,
                exc.__class__.__name__,
                _SCHEMA_WAIT_DELAY,
            )
            await asyncio.sleep(_SCHEMA_WAIT_DELAY)


def create_app(container: ApiContainer) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # поднять переопределения настроек и персоны в память + слушать чужие
        # записи (бот/другой инстанс) через тот же Postgres NOTIFY, что и бот.
        # С ожиданием готовности схемы: api может стартовать раньше миграций бота.
        await _load_state_when_ready(container)
        listener_tasks: list[asyncio.Task] = []
        if container.settings_listener is not None:
            listener_tasks.append(asyncio.create_task(container.settings_listener.run_forever()))
            logger.info("API: слушатель настроек запущен")
        if container.persona_listener is not None:
            listener_tasks.append(asyncio.create_task(container.persona_listener.run_forever()))
            logger.info("API: слушатель персон запущен")
        if container.entitlements_listener is not None:
            listener_tasks.append(
                asyncio.create_task(container.entitlements_listener.run_forever())
            )
            logger.info("API: слушатель тарифов запущен")
        try:
            yield
        finally:
            for task in listener_tasks:
                task.cancel()
            await container.engine.dispose()

    # Интерактивную схему (/docs, /redoc, /openapi.json) на публике не отдаём:
    # url=None полностью снимает роут. Включается только флагом в .env (dev).
    docs_kwargs: dict[str, Any] = (
        {}
        if container.settings.web_docs_enabled
        else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    )
    app = FastAPI(title="Poposya Web Panel API", version="0.1.0", lifespan=lifespan, **docs_kwargs)
    app.state.container = container

    # CORS: фронт (Vite) на другом origin шлёт куку-сессию -> нужен точный origin
    # и allow_credentials (со «*» браузер куки не пошлёт)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[container.settings.web_allowed_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def csrf_origin_guard(request: Request, call_next):
        """CSRF-защита state-changing запросов: Origin/Referer + double-submit токен.

        Кука сессии уже SameSite=Lax — базовая граница. Рубеж №2 — Origin: браузер
        при mutating-fetch всегда шлёт Origin; если он есть и не равен фронту — это
        cross-site POST, режем 403. Рубеж №3 — подписанный CSRF-токен: если Origin
        присутствует (браузерный запрос к нам) и сессия валидна, требуем эхо токена
        в заголовке X-CSRF-Token, совпадающее с привязанным к сессии значением.
        Кроссдоменный атакующий не может ни прочитать куку-токен, ни вычислить его.
        Origin ОТСУТСТВУЕТ (сервер-сервер, curl) — пропускаем: не браузерный вектор,
        кука и так Lax. Токен требуем только при наличии Origin, чтобы не ломать
        не-браузерных клиентов."""
        if request.method not in _CSRF_SAFE_METHODS and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin is None:
                referer = request.headers.get("referer")
                origin = _origin_of(referer) if referer else None
            if origin is not None and origin != container.settings.web_allowed_origin:
                return JSONResponse(
                    {"detail": "перекрёстный запрос отклонён (CSRF)"}, status_code=403
                )
            if origin is not None:
                token = request.cookies.get(SESSION_COOKIE)
                session = (
                    decode_session(
                        container.settings.web_session_secret,
                        token,
                        container.settings.web_session_version,
                    )
                    if token
                    else None
                )
                # только для аутентифицированных: неавторизованную мутацию всё
                # равно отвергнет зависимость (401), защищать её от CSRF нечего
                if session is not None and not csrf_token_valid(
                    container.settings.web_session_secret,
                    session.user_id,
                    session.epoch,
                    request.headers.get(CSRF_HEADER),
                ):
                    return JSONResponse({"detail": "нет или неверный CSRF-токен"}, status_code=403)
        return await call_next(request)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(settings.router)
    app.include_router(guilds.router)
    app.include_router(people.router)
    app.include_router(roles.router)
    app.include_router(moderation.router)
    app.include_router(appeals.router)
    app.include_router(music.router)
    app.include_router(finds.router)
    app.include_router(personas.router)
    app.include_router(entitlements.router)
    app.include_router(premium_keys.router)
    app.include_router(warden.router)
    return app
