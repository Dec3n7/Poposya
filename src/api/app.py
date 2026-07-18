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
from src.api.routers import auth, settings

logger = logging.getLogger(__name__)


def create_app(container: ApiContainer) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # поднять переопределения настроек в память + слушать чужие записи
        # (бот/другой инстанс) через тот же Postgres NOTIFY, что и бот
        await container.guild_settings.load_all()
        listener_task: asyncio.Task | None = None
        if container.settings_listener is not None:
            listener_task = asyncio.create_task(container.settings_listener.run_forever())
            logger.info("API: слушатель настроек запущен")
        try:
            yield
        finally:
            if listener_task is not None:
                listener_task.cancel()
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
    return app
