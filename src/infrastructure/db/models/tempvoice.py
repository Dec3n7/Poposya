from datetime import datetime

from sqlalchemy import BigInteger, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class TempVoiceChannelModel(Base):
    """Временный голосовой канал (фича «каморки»). Одна запись на канал;
    удаляется вместе с каналом, когда из него вышел последний человек."""

    __tablename__ = "temp_voice_channels"

    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
