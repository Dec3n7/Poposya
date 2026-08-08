from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class UnlockedAchievementModel(Base):
    """Открытая участником ачивка. Составной PK (участник+гильдия+ачивка) сам по
    себе даёт идемпотентность выдачи: повторный анлок отсекается ON CONFLICT."""

    __tablename__ = "unlocked_achievements"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    achievement_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    unlocked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (Index("ix_unlocked_ach_user", "guild_id", "user_id"),)
