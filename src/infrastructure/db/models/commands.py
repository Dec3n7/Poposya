from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class BotCommandModel(Base):
    """Командный мост панель→бот: панель кладёт команду (status='pending') и шлёт
    pg_notify; бот забирает по NOTIFY (и периодическим sweep'ом — переживает
    рестарт), выполняет Discord-действие и пишет статус + результат обратно.
    Панель опрашивает строку до терминального статуса. Заодно — аудит-лог всех
    админ-действий, выданных через панель."""

    __tablename__ = "bot_commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    command_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    # pending -> running -> done | failed
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    requested_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # быстрый выбор невыполненных для sweep'а
    __table_args__ = (Index("ix_bot_commands_status", "status", "id"),)
