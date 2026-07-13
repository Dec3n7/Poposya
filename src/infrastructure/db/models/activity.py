from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class MemberActivityModel(Base):
    __tablename__ = "member_activity"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    last_message_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AlbumPostModel(Base):
    __tablename__ = "album_posts"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class VoiceProgressModel(Base):
    __tablename__ = "voice_progress"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # накопительный итог для профиля («N часов со мной в войсе»); не обнуляется
    total_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ReminderModel(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (Index("ix_reminders_due", "due_at"),)
