"""FastAPI-приложение панели: сборка, CORS, жизненный цикл, роутеры.

Права и валидация — только здесь (бэкенд), фронт лишь отображает ответы.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from src.api.container import ApiContainer
from src.api.routers import (
    appeals,
    auth,
    finds,
    guilds,
    moderation,
    music,
    people,
    personas,
    roles,
    settings,
    warden,
)

logger = logging.getLogger(__name__)

# Холодный старт: миграции применяет БОТ, а api-процесс может подняться раньше
# и стукнуться в ещё несуществующие таблицы. Вместо crash-restart цикла ждём
# готовности схемы с бэкоффом (bot: start_period 60с). Здоровье БД проверяем
# самой рабочей загрузкой — не завязываясь на имя таблицы alembic.
_SCHEMA_WAIT_ATTEMPTS = 45
_SCHEMA_WAIT_DELAY = 2.0


async def _load_state_when_ready(container: ApiContainer) -> None:
    for attempt in range(1, _SCHEMA_WAIT_ATTEMPTS + 1):
        try:
            await container.guild_settings.load_all()
            await container.persona.load_all()
            await container.session_epochs.load_all()
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
    app.include_router(warden.router)
    return app
