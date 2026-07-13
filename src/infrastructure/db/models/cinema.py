from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class MovieEntryModel(Base):
    __tablename__ = "cinema_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    added_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overview: Mapped[str] = mapped_column(Text, nullable=False, default="")
    poster_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="listed")
    rating_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    rating_ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    avg_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ratings_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    poposya_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    poposya_review: Mapped[str] = mapped_column(Text, nullable=False, default="")
    watched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_cinema_entries_guild_status", "guild_id", "status"),
        Index("ix_cinema_entries_message", "message_id"),
        Index("ix_cinema_entries_rating_message", "rating_message_id"),
    )


class MovieVoteModel(Base):
    __tablename__ = "cinema_votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)  # +1 / -1

    __table_args__ = (
        UniqueConstraint("entry_id", "user_id", name="uq_cinema_vote"),
    )


class MovieNightModel(Base):
    __tablename__ = "cinema_nights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    poll_ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    candidates_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="poll")
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    poll_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    winner_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    winner_entry_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_cinema_nights_guild_status", "guild_id", "status"),
    )


class MovieNightVoteModel(Base):
    __tablename__ = "cinema_night_votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    night_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_id: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("night_id", "user_id", name="uq_cinema_night_vote"),
    )


class MovieRatingModel(Base):
    __tablename__ = "cinema_ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # score может быть NULL: пользователь вправе оставить отзыв без цифры
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review: Mapped[str | None] = mapped_column(Text, nullable=True)
    rated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("entry_id", "user_id", name="uq_cinema_rating"),
    )
