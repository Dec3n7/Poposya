from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index
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

    # индексы создаёт миграция 0017; объявляем и в модели, иначе autogenerate
    # считает их «лишними» и метадата расходится со схемой (и с тестовым
    # create_all, где их без этого не было)
    __table_args__ = (
        Index("ix_pending_kicks_kick_at", "kick_at"),
        Index("ix_pending_kicks_remind_at", "remind_at"),
    )
