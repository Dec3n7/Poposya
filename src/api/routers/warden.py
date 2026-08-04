"""Состояние внешнего сторожа (WARDEN) для панели.

Панель не считает ничего сама — она показывает то, что отдаёт сторож. Это
сознательно: интерпретация метрик живёт в одном месте, и панель не может
разойтись со сторожем в оценке «здоров ли бот».

Ходит сюда только бэкенд: токен — общий секрет двух сервисов, во фронт он не
попадает. Доступ — оператор: состояние контейнеров и латентность БД админам
чужих серверов видеть незачем.
"""

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.dependencies import get_container, require_operator
from src.api.security import Session

router = APIRouter(prefix="/api/warden", tags=["warden"])

_TIMEOUT = aiohttp.ClientTimeout(total=5)


class PauseBody(BaseModel):
    # сколько держать паузу; клампится и на стороне сторожа (1 мин .. 12 ч)
    minutes: int = Field(15, ge=1, le=720)


async def _warden_call(container, method: str, path: str, payload: dict | None = None) -> dict:
    """Единый прокси к HTTP-API сторожа: и чтение, и действия. Недоступность —
    не ошибка панели, а факт о системе: возвращаем `available: false`, а не 500."""
    settings = container.settings
    if not settings.warden_api_url or not settings.warden_api_token:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "WARDEN не подключён")
    url = settings.warden_api_url.rstrip("/") + path
    headers = {"X-Warden-Token": settings.warden_api_token}
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.request(method, url, headers=headers, json=payload) as resp:
                if resp.status == 401:
                    return {"available": False, "error": "неверный токен"}
                resp.raise_for_status()
                return {"available": True, **(await resp.json())}
    except Exception as exc:
        return {"available": False, "error": f"{type(exc).__name__}"}


@router.get("/status")
async def warden_status(
    _: Session = Depends(require_operator),
    container=Depends(get_container),
) -> dict:
    """Проксирует снимок состояния сторожа.

    Недоступность сторожа — это не ошибка панели, а факт о системе, поэтому
    отдаётся как обычный ответ с `available: false`. Иначе вкладка показывала
    бы «ошибка загрузки» там, где правильный ответ — «сторож не отвечает».
    """
    return await _warden_call(container, "GET", "/status")


@router.post("/pause")
async def warden_pause(
    body: PauseBody,
    _: Session = Depends(require_operator),
    container=Depends(get_container),
) -> dict:
    """Приостановить рестарты сторожа на N минут (перед деплоем/обслуживанием).
    Наблюдение и уведомления у сторожа продолжаются — гасятся только рестарты."""
    return await _warden_call(container, "POST", "/pause", {"minutes": body.minutes})


@router.post("/resume")
async def warden_resume(
    _: Session = Depends(require_operator),
    container=Depends(get_container),
) -> dict:
    """Снять паузу досрочно — вернуть сторожу право на рестарты."""
    return await _warden_call(container, "POST", "/resume")


@router.get("/enabled")
async def warden_enabled(
    _: Session = Depends(require_operator),
    container=Depends(get_container),
) -> dict:
    """Показывать ли вкладку вообще: без адреса сторожа её быть не должно."""
    settings = container.settings
    return {"enabled": bool(settings.warden_api_url and settings.warden_api_token)}
