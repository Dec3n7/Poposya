from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class SecretCodeModel(Base):
    __tablename__ = "secret_codes"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_secret_codes_code", "guild_id", "code"),)


class SecretRoomModel(Base):
    __tablename__ = "secret_rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    voice_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (Index("ix_secret_rooms_expires", "expires_at"),)


class RelationshipProfileModel(Base):
    __tablename__ = "relationship_profiles"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    points_awarded_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_award_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_exclusive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    frozen_by_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_dialog_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    survey_gender: Mapped[str] = mapped_column(Text, nullable=False, default="")
    survey_contact: Mapped[str] = mapped_column(Text, nullable=False, default="")
    survey_interests: Mapped[str] = mapped_column(Text, nullable=False, default="")
    survey_season: Mapped[str] = mapped_column(Text, nullable=False, default="")
    survey_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    birthday_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    birthday_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    birthday_reminded_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    birthday_congratulated_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    deep_dialogs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_decay_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        # инвариант «один Единственный на гильдию» — частичный уникальный
        # индекс, работает и на SQLite, и на PostgreSQL (ТЗ 8.5)
        Index(
            "uq_relationship_exclusive_per_guild",
            "guild_id",
            unique=True,
            sqlite_where=text("is_exclusive = 1"),
            postgresql_where=text("is_exclusive"),
        ),
        Index("ix_relationship_guild_points", "guild_id", "points"),
    )


class DialogSummaryModel(Base):
    __tablename__ = "dialog_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (Index("ix_dialog_summaries_user", "guild_id", "user_id", "created_at"),)
