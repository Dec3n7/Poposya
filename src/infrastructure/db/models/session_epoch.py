from sqlalchemy import BigInteger, Integer
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class WebSessionEpochModel(Base):
    """Эпоха веб-сессий пользователя для СЕРВЕРНОГО отзыва.

    JWT несёт claim `ep`; при проверке сверяется с текущей эпохой пользователя.
    Бамп эпохи (real logout / операторский отзыв) мгновенно делает все прежние
    токены пользователя недействительными. Отсутствие строки = эпоха 0."""

    __tablename__ = "web_session_epoch"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
