from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class TrackedGameModel(Base):
    __tablename__ = "tracked_games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    appid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # gid и время последней уже объявленной новости — «отметка»
    last_news_gid: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_news_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    added_by: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_tracked_games_guild", "guild_id"),
        UniqueConstraint("guild_id", "appid", name="uq_tracked_game"),
    )
