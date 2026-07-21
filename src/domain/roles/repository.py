from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.roles.entities import GuildRole, RoleMeta, SavedRoleTemplate, TemplateRole


class IRoleRepository(ABC):
    @abstractmethod
    async def replace_guild_roles(
        self, guild_id: int, roles: list[GuildRole], now: datetime
    ) -> None:
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

    # --- носители ролей (member_roles) ---

    @abstractmethod
    async def replace_member_roles(self, guild_id: int, holders: dict[int, list[int]]) -> None:
        """Полная пере-синхронизация носителей сервера (бэкфилл): user_id -> его role_id."""

    @abstractmethod
    async def set_member_roles(self, guild_id: int, user_id: int, role_ids: list[int]) -> None:
        """Роли одного участника изменились — переложить его строки."""

    @abstractmethod
    async def delete_member(self, guild_id: int, user_id: int) -> None:
        """Участник вышел — убрать все его строки."""

    @abstractmethod
    async def holder_counts(self, guild_id: int) -> dict[int, int]:
        """role_id -> число носителей на сервере."""

    @abstractmethod
    async def member_role_ids(self, guild_id: int, user_id: int) -> list[int]:
        """id ролей участника из зеркала."""

    # --- сохранённые шаблоны ролей (панель) ---

    @abstractmethod
    async def save_template(
        self, guild_id: int, name: str, roles: list[TemplateRole], now: datetime
    ) -> SavedRoleTemplate:
        """Сохранить/обновить именованный шаблон (upsert по guild_id+name)."""

    @abstractmethod
    async def list_templates(self, guild_id: int) -> list[SavedRoleTemplate]:
        """Все сохранённые шаблоны сервера (новые сверху)."""

    @abstractmethod
    async def get_template(self, guild_id: int, template_id: int) -> SavedRoleTemplate | None:
        """Один шаблон сервера по id или None."""

    @abstractmethod
    async def delete_template(self, guild_id: int, template_id: int) -> bool:
        """Удалить шаблон; False — его и не было."""
