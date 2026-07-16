from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class PendingKickModel(Base):
    """Запланированный авто-кик новичка (фича «остаться или уйти»).
    Одна запись на пару (сервер, пользователь)."""

    __tablename__ = "pending_kicks"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    remind_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    kick_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    reminded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
