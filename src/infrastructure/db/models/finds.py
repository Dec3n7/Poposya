from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class NightFindModel(Base):
    __tablename__ = "night_finds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    location_id: Mapped[str] = mapped_column(Text, nullable=False)
    item_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    claimed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_night_finds_guild_active", "guild_id", "claimed_by", "expires_at"),
        Index("ix_night_finds_message", "message_id"),
    )


class CollectionItemModel(Base):
    __tablename__ = "user_collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    item_id: Mapped[str] = mapped_column(Text, nullable=False)
    obtained_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    gifted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_user_collections_owner", "guild_id", "user_id", "obtained_at"),
    )


class FindAttemptModel(Base):
    __tablename__ = "find_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # claim | walk
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    find_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_find_attempts_user", "guild_id", "user_id", "kind", "attempted_at"),
        Index("ix_find_attempts_find", "find_id", "user_id"),
    )
