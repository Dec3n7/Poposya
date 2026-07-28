"""Outbox критичных событий: сериализация, реестр типов и диспетчер.

Гарантия — at-least-once: событие может быть опубликовано повторно
(упали между commit и отметкой published_at), поэтому подписчики критичных
событий обязаны быть идемпотентными. Синхронизация Discord-ролей — сверка
состояния, повторная доставка ей безвредна."""

import asyncio
import json
import logging
import time
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
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
    # без строкового default `.default` вернёт сентинел dataclasses.MISSING:
    # событие зарегистрировалось бы под ним, а dispatcher по реальному
    # event_type его бы не нашёл — deserialize_event вернул бы None и пометил
    # запись «исчерпавшей попытки». Тихая потеря критичного события; ловим
    # громко и на импорте, а не годы спустя по растущему outbox.dead.
    if not isinstance(event_type, str):
        raise TypeError(
            f"{cls.__name__}.event_type обязан иметь строковый default "
            '(например: event_type: str = "feature.happened")'
        )
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
        # отметка живости для health-метрик: задача может быть «не завершена»,
        # но при этом висеть внутри прохода — по одному факту существования
        # таска отличить работающий диспетчер от зависшего нельзя
        self._last_pass_at: float | None = None

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
            now = datetime.now(UTC).replace(tzinfo=None)
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

    async def backlog_stats(self) -> dict:
        """Состояние очереди для внешнего мониторинга.

        `dead` — события, исчерпавшие попытки: диспетчер их больше не берёт, и
        сами они уже никогда не уедут. Тихо растущий `dead` означает потерянные
        доменные события при формально исправном боте.
        """
        async with self._session_factory() as session:
            pending_row = (
                await session.execute(
                    select(
                        func.count(OutboxEventModel.id),
                        func.min(OutboxEventModel.occurred_at),
                    ).where(
                        OutboxEventModel.published_at.is_(None),
                        OutboxEventModel.attempts < self._max_attempts,
                    )
                )
            ).one()
            dead = (
                await session.execute(
                    select(func.count(OutboxEventModel.id)).where(
                        OutboxEventModel.published_at.is_(None),
                        OutboxEventModel.attempts >= self._max_attempts,
                    )
                )
            ).scalar_one()

        pending, oldest = pending_row
        # occurred_at лежит наивным UTC (см. outbox_row_for) — сравниваем с
        # наивным «сейчас», иначе вычитание разнесёт по TypeError
        oldest_age = (
            round((datetime.now(UTC).replace(tzinfo=None) - oldest).total_seconds(), 1)
            if oldest is not None
            else None
        )
        return {
            "pending": int(pending or 0),
            "dead": int(dead or 0),
            "oldest_pending_age_seconds": oldest_age,
            "interval_seconds": self._interval,
            "seconds_since_last_pass": (
                round(time.monotonic() - self._last_pass_at, 1)
                if self._last_pass_at is not None
                else None
            ),
        }

    async def run_forever(self) -> None:
        # первый проход после паузы: подписчики регистрируются при старте когов
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.dispatch_once()
            except Exception:
                logger.exception("Outbox-диспетчер упал на проходе")
            finally:
                # отметка ставится и после сбойного прохода: цикл жив, а качество
                # проходов видно по отдельной метрике ошибок
                self._last_pass_at = time.monotonic()
