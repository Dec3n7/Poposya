from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime

from src.domain.moderation.entities import ModCase, TempBan, Warn


class IWarnRepository(ABC):
    @abstractmethod
    async def add(self, warn: Warn) -> None: ...

    @abstractmethod
    async def count(self, user_id: int, guild_id: int, since: datetime | None = None) -> int:
        """Число варнов участника. since != None — считаем только выданные позже
        (затухание: старые варны не считаются к порогу)."""

    @abstractmethod
    async def list(self, user_id: int, guild_id: int) -> list[Warn]: ...

    @abstractmethod
    async def list_guild_counts(self, guild_id: int) -> Sequence[tuple[int, int, datetime]]:
        """Участники сервера с активными варнами: (user_id, число, последний_варн).
        По убыванию числа варнов — для серверной сводки на панели."""

    @abstractmethod
    async def clear(self, user_id: int, guild_id: int) -> None: ...


class ITempBanRepository(ABC):
    """Временные баны переживают рестарт: таймер продолжается из БД."""

    @abstractmethod
    async def add(self, ban: TempBan) -> None: ...

    @abstractmethod
    async def list_active(self, guild_id: int, now: datetime) -> list[TempBan]: ...

    @abstractmethod
    async def pop_expired(self, now: datetime) -> list[TempBan]:
        """Удаляет и возвращает просроченные баны (для авторазбана)."""

    @abstractmethod
    async def remove(self, user_id: int, guild_id: int) -> bool: ...


class IModCaseRepository(ABC):
    """Единый журнал действий модерации: сюда пишут и бот, и панель (мост).
    Только запись и чтение истории — редактирования/удаления нет by design."""

    @abstractmethod
    async def add(self, case: ModCase) -> ModCase:
        """Сохраняет кейс и возвращает его же с проставленным id."""

    @abstractmethod
    async def list_for_user(self, guild_id: int, user_id: int, limit: int = 50) -> list[ModCase]:
        """История по участнику, свежие сверху."""

    @abstractmethod
    async def count_for_user(self, guild_id: int, user_id: int, actions: Sequence[str]) -> int:
        """Сколько раз к участнику применялись действия из actions (для эскалации)."""
