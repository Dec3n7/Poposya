from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class GuildRoleModel(Base):
    """Зеркало роли Discord. Источник правды — сам Discord; бот поддерживает
    эту копию актуальной (бэкфилл на старте + gateway-события), чтобы панель
    читала роли и иерархию без обращения к шлюзу."""

    __tablename__ = "guild_roles"

    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    color: Mapped[int] = mapped_column(Integer, nullable=False)  # 0 = без цвета
    hoist: Mapped[bool] = mapped_column(Boolean, nullable=False)
    mentionable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    managed: Mapped[bool] = mapped_column(Boolean, nullable=False)  # роль интеграции/бота — не трогать
    permissions: Mapped[int] = mapped_column(BigInteger, nullable=False)  # битовое поле прав
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class GuildRoleMetaModel(Base):
    """Одна строка на сервер: где проходит граница возможностей бота (позиция его
    высшей роли — выше неё роли ему недоступны) и когда зеркало синхронизировали."""

    __tablename__ = "guild_role_meta"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bot_top_position: Mapped[int] = mapped_column(Integer, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class MemberRoleModel(Base):
    """Кто носит какую роль (кроме @everyone). Строка на пару участник×роль;
    бот держит актуальной событиями участников. Нужна для счётчиков носителей и
    выдачи/снятия ролей из панели."""

    __tablename__ = "member_roles"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
