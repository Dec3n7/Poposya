from datetime import datetime

from sqlalchemy import BigInteger, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class GuildDepartureModel(Base):
    """Отметка «бот покинул сервер <left_at>» для отложенного удаления данных.

    При выходе бота строка ставится (upsert), при возврате — снимается. Фоновый
    цикл стирает все данные сервера, когда `left_at` старше окна отсрочки
    (по умолчанию 30 дней) — защита от случайного кика/переинвайта."""

    __tablename__ = "guild_departures"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    left_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
