"""FastAPI-приложение панели: сборка, CORS, жизненный цикл, роутеры.

Права и валидация — только здесь (бэкенд), фронт лишь отображает ответы.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.container import ApiContainer
from src.api.routers import (
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


def create_app(container: ApiContainer) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # поднять переопределения настроек и персоны в память + слушать чужие
        # записи (бот/другой инстанс) через тот же Postgres NOTIFY, что и бот
        await container.guild_settings.load_all()
        await container.persona.load_all()
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

    app = FastAPI(title="Poposya Web Panel API", version="0.1.0", lifespan=lifespan)
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
    app.include_router(music.router)
    app.include_router(finds.router)
    app.include_router(personas.router)
    app.include_router(warden.router)
    return app
