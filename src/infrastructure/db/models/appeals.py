from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class AppealModel(Base):
    """Апелляция на наказание: одна запись на обжалование, статус меняется при
    разборе (pending → approved/rejected)."""

    __tablename__ = "appeals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)  # ban | tempban | mute | kick
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    original_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    review_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    resolver_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_appeals_guild_status", "guild_id", "status"),)
