from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class BotProfileModel(Base):
    """Пер-серверный профиль бота (одна строка на гильдию)."""

    __tablename__ = "bot_profile"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nick: Mapped[str] = mapped_column(Text, nullable=False, default="")
    avatar_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    banner_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # загруженный+обрезанный аватар (base64 data-URL) — приоритетнее avatar_url
    avatar_data: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
