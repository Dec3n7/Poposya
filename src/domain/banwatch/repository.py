from abc import ABC, abstractmethod

from src.domain.banwatch.entities import ServerBan


class IServerBanRepository(ABC):
    """Баны по серверам — общая таблица всех гильдий, где стоит бот."""

    @abstractmethod
    async def upsert(self, ban: ServerBan) -> None:
        """Добавить/обновить бан по паре (guild_id, user_id)."""

    @abstractmethod
    async def remove(self, guild_id: int, user_id: int) -> None:
        """Снять бан (разбан) на конкретном сервере."""

    @abstractmethod
    async def list_for_user(self, user_id: int) -> list[ServerBan]:
        """Все баны пользователя по всем серверам (для карточки/checkuser)."""

    @abstractmethod
    async def replace_guild(self, guild_id: int, bans: list[ServerBan]) -> None:
        """Синхронизация бэкфилла: заменить все баны сервера текущим списком."""

    @abstractmethod
    async def flagged_candidates(
        self, exclude_guild_id: int, threshold: int
    ) -> list[tuple[int, int]]:
        """(user_id, число серверов) для тех, кто забанен на >= threshold серверах,
        КРОМЕ exclude_guild_id. Множество маленькое (только злостные), поэтому
        членство на сервере проверяем уже поверх этого списка."""
