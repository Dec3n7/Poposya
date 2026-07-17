from abc import ABC, abstractmethod

from src.domain.tempvoice.entities import TempChannel


class ITempVoiceRepository(ABC):
    @abstractmethod
    async def register(self, channel: TempChannel) -> None:
        """Запоминает созданный канал."""

    @abstractmethod
    async def release(self, channel_id: int) -> bool:
        """Забывает канал. False — его и не было."""

    @abstractmethod
    async def get(self, channel_id: int) -> TempChannel | None:
        """Канал по id; None — канал не наш."""

    @abstractmethod
    async def set_owner(self, channel_id: int, owner_id: int) -> None:
        """Меняет владельца («Забрать»)."""

    @abstractmethod
    async def list_for_guild(self, guild_id: int) -> list[TempChannel]:
        """Все каналы сервера — чтобы подмести осиротевшие после рестарта."""

    @abstractmethod
    async def count_for_guild(self, guild_id: int) -> int:
        """Сколько каналов сервера живо сейчас (потолок на сервер)."""
