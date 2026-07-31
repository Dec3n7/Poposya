from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class TrackedRepoModel(Base):
    __tablename__ = "tracked_repos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # id и время публикации последнего уже объявленного релиза — «отметка»
    last_release_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    etag: Mapped[str] = mapped_column(Text, nullable=False, default="")
    added_by: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_tracked_repos_guild", "guild_id"),
        UniqueConstraint("guild_id", "owner", "name", name="uq_tracked_repo"),
    )
