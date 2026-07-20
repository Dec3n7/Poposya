from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.roles.entities import GuildRole, RoleMeta


class IRoleRepository(ABC):
    @abstractmethod
    async def replace_guild_roles(self, guild_id: int, roles: list[GuildRole], now: datetime) -> None:
        """Полная пере-синхронизация зеркала ролей сервера (бэкфилл): старые
        строки сервера сносятся, кладутся переданные."""

    @abstractmethod
    async def upsert_role(self, role: GuildRole, now: datetime) -> None:
        """Одна роль создана/изменена — обновить её строку."""

    @abstractmethod
    async def delete_role(self, guild_id: int, role_id: int) -> None:
        """Роль удалена — убрать её из зеркала."""

    @abstractmethod
    async def set_meta(self, meta: RoleMeta) -> None:
        """Обновить границу возможностей бота + отметку синхронизации."""

    @abstractmethod
    async def list_roles(self, guild_id: int) -> list[GuildRole]:
        """Все роли сервера из зеркала."""

    @abstractmethod
    async def get_meta(self, guild_id: int) -> RoleMeta | None:
        """Мета сервера или None, если синхронизации ещё не было."""
