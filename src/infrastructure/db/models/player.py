from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class PlayerStateModel(Base):
    """Снапшот живого плеера гильдии (одна строка на гильдию). current/queue —
    JSON. Перезаписывается ботом на каждое изменение."""

    __tablename__ = "player_state"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    current: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON PlayerTrack
    queue: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list
    position_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    position_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    repeat: Mapped[str] = mapped_column(Text, nullable=False, default="off")
    volume: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
