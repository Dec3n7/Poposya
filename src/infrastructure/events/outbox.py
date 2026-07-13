"""Outbox критичных событий: сериализация, реестр типов и диспетчер.

Гарантия — at-least-once: событие может быть опубликовано повторно
(упали между commit и отметкой published_at), поэтому подписчики критичных
событий обязаны быть идемпотентными. Синхронизация Discord-ролей — сверка
состояния, повторная доставка ей безвредна."""

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.domain.events.base import CriticalDomainEvent, DomainEvent
from src.domain.events.bus import IEventBus
from src.domain.relationship.events import ExclusiveTransferred, RelationshipRoleChanged
from src.infrastructure.db.models.outbox import OutboxEventModel

logger = logging.getLogger(__name__)

# Реестр критичных типов: event_type -> класс. Новый CriticalDomainEvent
# обязан быть добавлен сюда, иначе диспетчер не сможет его восстановить.
_REGISTRY: dict[str, type[DomainEvent]] = {}


def _register(cls: type[DomainEvent]) -> None:
    event_type = cls.__dataclass_fields__["event_type"].default
    _REGISTRY[event_type] = cls


_register(RelationshipRoleChanged)
_register(ExclusiveTransferred)


def serialize_event(event: DomainEvent) -> str:
    data = asdict(event)
    data["event_id"] = str(data["event_id"])
    data["occurred_at"] = data["occurred_at"].isoformat()
    return json.dumps(data, ensure_ascii=False)


def deserialize_event(event_type: str, payload: str) -> DomainEvent | None:
    """None — тип неизвестен (событие убрали из кода); такие записи
    диспетчер помечает исчерпавшими попытки."""
    cls = _REGISTRY.get(event_type)
    if cls is None:
        return None
    data = json.loads(payload)
    data["event_id"] = UUID(data["event_id"])
    data["occurred_at"] = datetime.fromisoformat(data["occurred_at"])
    return cls(**data)


def outbox_row_for(event: CriticalDomainEvent) -> OutboxEventModel:
    return OutboxEventModel(
        event_id=str(event.event_id),
        event_type=event.event_type,
        payload=serialize_event(event),
        occurred_at=event.occurred_at.replace(tzinfo=None),
    )


class OutboxDispatcher:
    """Фоновая публикация событий, не дошедших до шины: процесс упал между
    commit и publish — диспетчер доставит их после рестарта."""

    _BATCH = 50

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: IEventBus,
        interval_seconds: int = 60,
        max_attempts: int = 10,
    ):
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._interval = interval_seconds
        self._max_attempts = max_attempts

    async def dispatch_once(self) -> int:
        """Публикует пачку неопубликованных; возвращает число доставленных."""
        delivered = 0
        async with self._session_factory() as session:
            stmt = (
                select(OutboxEventModel)
                .where(
                    OutboxEventModel.published_at.is_(None),
                    OutboxEventModel.attempts < self._max_attempts,
                )
                .order_by(OutboxEventModel.id)
                .limit(self._BATCH)
            )
            rows = (await session.execute(stmt)).scalars().all()
            if not rows:
                return 0
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            for row in rows:
                event = deserialize_event(row.event_type, row.payload)
                if event is None:
                    logger.warning(
                        "Outbox: неизвестный тип события, откладываю навсегда",
                        extra={"event_type": row.event_type, "outbox_id": row.id},
                    )
                    row.attempts = self._max_attempts
                    continue
                try:
                    await self._event_bus.publish(event)
                except Exception:
                    row.attempts += 1
                    logger.exception(
                        "Outbox: публикация не удалась",
                        extra={"event_type": row.event_type, "attempts": row.attempts},
                    )
                    continue
                row.published_at = now
                delivered += 1
            await session.commit()
        if delivered:
            logger.info("Outbox: досталось из очереди событий: %d", delivered)
        return delivered

    async def run_forever(self) -> None:
        # первый проход после паузы: подписчики регистрируются при старте когов
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.dispatch_once()
            except Exception:
                logger.exception("Outbox-диспетчер упал на проходе")
