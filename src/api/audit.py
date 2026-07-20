"""Хелпер записи в журнал действий панели.

Зовётся в конце write-эндпоинтов. Сбой записи журнала НЕ должен ронять само
действие — поэтому всё внутри try/except (аудит — побочный, не критичный путь).
"""

import json
import logging

from src.api.container import ApiContainer
from src.domain.audit.entities import AuditEntry

logger = logging.getLogger(__name__)


async def record_audit(
    container: ApiContainer,
    guild_id: int,
    actor_id: int,
    action: str,
    *,
    target: str | int | None = None,
    details: dict | None = None,
    result: str | None = "ok",
) -> None:
    try:
        payload = json.dumps(details, ensure_ascii=False) if details else None
        await container.append_audit.execute(
            AuditEntry(
                guild_id=guild_id,
                actor_id=actor_id,
                action=action,
                target=str(target) if target is not None else None,
                details=payload,
                result=result,
            )
        )
    except Exception:
        logger.warning("Не удалось записать аудит: %s", action, exc_info=True)
