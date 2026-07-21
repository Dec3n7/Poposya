from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class PersonaModel(Base):
    """Именованная библиотека текста/личности бота. Дефолтная «Попося»
    (is_default) неудаляема; её пустые prompt/chime_prompt/attributes означают
    «резолв из кода» (см. persona_service). FK нет — как и везде в проекте."""

    __tablename__ = "personas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    chime_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attributes: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)


class PersonaPhraseModel(Base):
    """Override одной строки каталога фраз для персоны. Отсутствие строки =
    дефолт из PHRASE_SPECS. value — JSON (str | list[str] | dict[str, str])."""

    __tablename__ = "persona_phrases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    persona_id: Mapped[int] = mapped_column(Integer, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    mode: Mapped[str] = mapped_column(Text, nullable=False, default="ai_then_static")

    __table_args__ = (UniqueConstraint("persona_id", "key", name="uq_persona_phrase"),)


class GuildPersonaModel(Base):
    """Назначение персоны серверу (одна активная персона на гильдию).
    Отсутствие строки = дефолтная «Попося»."""

    __tablename__ = "guild_persona"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    persona_id: Mapped[int] = mapped_column(Integer, nullable=False)
