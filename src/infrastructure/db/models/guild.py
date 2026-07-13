from sqlalchemy import BigInteger, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class GuildSettingModel(Base):
    """Переопределение настройки на конкретном сервере (ключ -> значение-текст).
    Отсутствие строки = используется глобальный дефолт из .env."""

    __tablename__ = "guild_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("guild_id", "key", name="uq_guild_setting"),)
