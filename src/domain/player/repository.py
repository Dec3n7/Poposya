from abc import ABC, abstractmethod

from src.domain.player.entities import PlayerState


class IPlayerStateRepository(ABC):
    """Снапшот живого плеера: одна строка на гильдию, перезаписывается (upsert).
    Пишет бот на каждое изменение состояния, читает панель."""

    @abstractmethod
    async def save(self, state: PlayerState) -> None: ...

    @abstractmethod
    async def get(self, guild_id: int) -> PlayerState | None: ...
