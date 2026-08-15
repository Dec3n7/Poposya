"""Реестр каталога фраз и дефолтов персоны - единственный источник правды по
дефолтам (аналог SETTING_KEYS/GuildSettings для настроек).

Принцип «в БД только override»: PHRASE_SPECS.default = текущее поведение
Попоси. persona_phrases в БД лишь переопределяет отдельные ключи; отсутствие
строки = дефолт отсюда.

Сами фразы живут по доменам в persona/phrases/<category>.py (каждый экспортит
список SPECS); здесь они собираются в единый PHRASE_SPECS. Базовые примитивы
(PhraseSpec, _spec, режимы) - в persona/phrases/_base.py; ре-экспортятся ниже
для обратной совместимости импортов из registry."""

from src.application.persona.phrases import (
    achievements,
    activity,
    ai_chat,
    appeals,
    cinema,
    config,
    digest,
    errors,
    finds,
    fun,
    introduce,
    moderation,
    music,
    onboarding,
    relationship,
    secret_room,
    staykick,
    tempvoice,
)
from src.application.persona.phrases._base import (  # noqa: F401  - ре-экспорт
    AI_ONLY,
    ALL_MODES,
    DEFAULT_MODE,
    STATIC_MODES,
    PhraseSpec,
    _spec,
)

# --- мягкая личность: дефолты в коде, override - в Persona.attributes (P3) ---
DEFAULT_PERSONA_NAME = "Попося"

DEFAULT_ATTRIBUTES: dict[str, object] = {
    "display_name": "Попося",  # имя-в-тексте
    "signature": "✂️👁🖤",  # подпись-эмодзи
    "accent_color": 0x9B59B6,  # accent эмбедов (см. infrastructure/discord/accent.py)
    "presence": [],  # строки Discord-присутствия; пусто = встроенный канон Попоси
}

# лимиты атрибутов (валидация set_identity; панель ограничивает те же значения)
IDENTITY_TEXT_MAX = 64  # display_name / signature
PRESENCE_LINE_MAX = 128  # одна строка статуса (лимит Discord)
PRESENCE_LINES_MAX = 20

# Порядок кортежа = порядок первого появления категорий в исходном реестре -
# сохраняет PHRASE_CATEGORIES (вкладки панели) и относительный порядок ключей.
PHRASE_SPECS: dict[str, PhraseSpec] = {
    s.key: s
    for s in (
        *activity.SPECS,
        *onboarding.SPECS,
        *finds.SPECS,
        *secret_room.SPECS,
        *introduce.SPECS,
        *ai_chat.SPECS,
        *music.SPECS,
        *achievements.SPECS,
        *cinema.SPECS,
        *tempvoice.SPECS,
        *fun.SPECS,
        *moderation.SPECS,
        *config.SPECS,
        *staykick.SPECS,
        *relationship.SPECS,
        *errors.SPECS,
        *digest.SPECS,
        *appeals.SPECS,
    )
}

PHRASE_KEYS: tuple[str, ...] = tuple(PHRASE_SPECS.keys())
# порядок категорий сохраняем (для вкладок панели)
PHRASE_CATEGORIES: tuple[str, ...] = tuple(
    dict.fromkeys(spec.category for spec in PHRASE_SPECS.values())
)


def default_phrase(key: str, **variables: object) -> object:
    """Дефолт ключа прямо из реестра (без БД/override) - application-фолбэк для
    кода, которому не проброшен PersonaService (ChatService в тестах). Строки с
    variables форматируются мягко: кривой плейсхолдер не роняет вызов. Совпадает
    по семантике с PhraseResolver.phrase() на «нет override»."""
    value: object = PHRASE_SPECS[key].default
    if variables and isinstance(value, str):
        try:
            return value.format(**variables)
        except (KeyError, IndexError, ValueError):
            return value
    return value
