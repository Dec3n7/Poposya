from datetime import date

from sqlalchemy import BigInteger, Date, Float, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class GuildMetricDailyModel(Base):
    """Суточный снапшот одной метрики сервера. Узкая схема: строка на
    (guild_id, day, metric) — новая метрика не требует миграции."""

    __tablename__ = "guild_metrics_daily"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    metric: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
