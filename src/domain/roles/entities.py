from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class GuildRole:
    """Снимок роли Discord в зеркале панели."""

    guild_id: int
    role_id: int
    name: str
    color: int  # 0 = без цвета
    hoist: bool
    mentionable: bool
    position: int
    managed: bool  # роль интеграции/бустов/бота — Discord не даёт её менять
    permissions: int  # битовое поле прав


@dataclass(frozen=True)
class RoleMeta:
    """Граница возможностей бота на сервере + отметка синхронизации зеркала."""

    guild_id: int
    bot_user_id: int
    bot_top_position: int  # позиция высшей роли бота: выше — роли ему недоступны
    synced_at: datetime


@dataclass(frozen=True)
class TemplateRole:
    """Одна роль внутри сохранённого шаблона: только косметика, без прав."""

    name: str
    color: int | None  # None = без цвета
    hoist: bool
    mentionable: bool


@dataclass(frozen=True)
class SavedRoleTemplate:
    """Именованный набор ролей, сохранённый администратором для сервера."""

    id: int
    guild_id: int
    name: str
    roles: tuple[TemplateRole, ...]
    created_at: datetime
