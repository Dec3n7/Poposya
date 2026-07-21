from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Persona:
    """Именованная библиотека текста/личности бота.

    Пустые prompt/chime_prompt/attributes у дефолтной «Попоси» означают «резолв
    из кода» (файл промпта, PHRASE_SPECS, DEFAULT_ATTRIBUTES) — тот же принцип
    «в БД только override», что и у guild_settings."""

    id: int
    name: str
    is_default: bool = False
    prompt: str = ""
    chime_prompt: str = ""
    attributes: dict[str, object] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class PersonaPhrase:
    """Override одной строки каталога фраз для персоны.

    value — str | list[str] | dict[str, str]. mode управляет генерацией в когах
    (ai_then_static | static | silent) и вступает в силу при рефакторинге когов
    в P4; отсутствие override = дефолт из PHRASE_SPECS."""

    persona_id: int
    key: str
    value: object
    mode: str = "ai_then_static"
