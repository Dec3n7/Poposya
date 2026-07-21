from datetime import date

from sqlalchemy import BigInteger, Date, Integer, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class MessageActivityModel(Base):
    """Почасовой счётчик сообщений сервера. Узкая агрегат-схема: строка на
    (guild_id, дата, час в UTC) — без пользователя и без содержимого."""

    __tablename__ = "message_activity"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bucket_date: Mapped[date] = mapped_column(Date, primary_key=True)
    bucket_hour: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)


class VoiceActivityModel(Base):
    """Почасовое время присутствия в голосовых каналах: суммарные человеко-секунды
    (сколько живых участников × сколько секунд сидели) на (guild_id, дата, час UTC).
    Та же приватная агрегат-схема, что и у сообщений — без пользователя."""

    __tablename__ = "voice_activity"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bucket_date: Mapped[date] = mapped_column(Date, primary_key=True)
    bucket_hour: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    seconds: Mapped[int] = mapped_column(Integer, nullable=False)
