"""Серверный отзыв веб-сессий через «эпоху» на пользователя.

Токены stateless (JWT), поэтому индивидуальный отзыв невозможен без серверного
состояния. Держим по одной строке на пользователя — его текущую эпоху; она
вшивается в токен (claim `ep`) при выдаче и сверяется при каждом запросе. Бамп
эпохи (real logout / операторский отзыв) делает все прежние токены пользователя
недействительными.

Кэш в памяти (как GuildSettingsService): читается синхронно на горячем пути
проверки сессии. Писатель ОДИН — процесс API (logout/revoke — его эндпоинты),
и читатель тоже только API, поэтому межпроцессный NOTIFY не нужен. При росте до
нескольких реплик API добавить pg_notify-инвалидацию (как у настроек)."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infrastructure.db.models.session_epoch import WebSessionEpochModel

logger = logging.getLogger(__name__)


class SessionEpochService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory
        self._cache: dict[int, int] = {}

    async def load_all(self) -> None:
        async with self._sf() as session:
            rows = (await session.execute(select(WebSessionEpochModel))).scalars().all()
        self._cache = {row.user_id: row.epoch for row in rows}
        logger.info("Эпохи веб-сессий загружены: %d", len(self._cache))

    def epoch_of(self, user_id: int) -> int:
        """Текущая эпоха пользователя (0, если отзывов не было). Синхронно, из кэша."""
        return self._cache.get(user_id, 0)

    async def bump(self, user_id: int) -> int:
        """Отозвать все сессии пользователя: +1 к эпохе (upsert). Возвращает новую."""
        async with self._sf() as session:
            row = await session.get(WebSessionEpochModel, user_id)
            if row is None:
                new_epoch = 1
                session.add(WebSessionEpochModel(user_id=user_id, epoch=new_epoch))
            else:
                new_epoch = row.epoch + 1
                row.epoch = new_epoch
            await session.commit()
        self._cache[user_id] = new_epoch
        logger.info("Веб-сессии пользователя отозваны", extra={"user_id": user_id})
        return new_epoch
