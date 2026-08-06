"""Общий self._p(...) — строковая фраза из каталога персоны сервера.

Убирает 15 дословных копий одного метода по когам. Хост-класс должен иметь
атрибут ``persona`` (PhraseResolver: PersonaService в проде, RegistryPersona в
тест-заглушках). guild_id/key — позиционные (positional-only): у настроек есть
плейсхолдер с именем «key», который иначе столкнулся бы с именем параметра
(см. PhraseResolver.phrase).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.infrastructure.persona_service import PhraseResolver


class PersonaPhraseMixin:
    """Даёт ``self._p(guild_id, key, **vars)`` любому классу с атрибутом ``persona``."""

    persona: PhraseResolver

    def _p(self, guild_id: int | None, key: str, /, **variables: object) -> str:
        """Строковая фраза каталога персоны сервера."""
        return str(self.persona.phrase(guild_id or 0, key, **variables))
