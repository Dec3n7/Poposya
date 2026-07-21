"""Реестр каталога фраз и дефолтов персоны — единственный источник правды по
дефолтам (аналог SETTING_KEYS/GuildSettings для настроек).

Принцип «в БД только override»: PHRASE_SPECS.default = текущее поведение
Попоси. persona_phrases в БД лишь переопределяет отдельные ключи; отсутствие
строки = дефолт отсюда. Так после миграции поведение бота не меняется.

P1 — «промпт + 1-2 категории»: промпт хранится колонкой Persona.prompt (не
фраза), а здесь заведены реальные (1:1 с текущими константами когов) дефолты
для проверки механизма резолва. Вынос ВСЕХ ~300 строк в этот реестр и
подключение когов к резолву — это P4 (тогда же дублирующие константы когов
удаляются)."""

from dataclasses import dataclass

# --- мягкая личность: дефолты в коде, override — в Persona.attributes (P3) ---
DEFAULT_PERSONA_NAME = "Попося"

DEFAULT_ATTRIBUTES: dict[str, object] = {
    "display_name": "Попося",  # имя-в-тексте
    "signature": "✂️👁🖤",  # подпись-эмодзи
    "accent_color": 0x9B59B6,  # accent эмбедов (_EMBED_COLOR)
    "presence": [],  # строки Discord-присутствия (применяются в P3)
}

# --- режимы блока (mode персона-фразы); вступают в силу в когах в P4 ---
ALL_MODES: tuple[str, ...] = ("ai_then_static", "static", "silent")
DEFAULT_MODE = "ai_then_static"


@dataclass(frozen=True)
class PhraseSpec:
    """Описание одного ключа каталога: где живёт, какого рода значение, дефолт,
    какие плейсхолдеры допустимы и какие режимы разрешены."""

    key: str
    category: str
    kind: str  # "str" | "template" | "list" | "dict"
    default: object  # str | list[str] | dict[str, str]
    label: str = ""
    placeholders: frozenset[str] = frozenset()
    allowed_modes: tuple[str, ...] = ALL_MODES


def _spec(
    key: str,
    category: str,
    kind: str,
    default: object,
    *,
    label: str = "",
    placeholders: tuple[str, ...] = (),
    allowed_modes: tuple[str, ...] = ALL_MODES,
) -> PhraseSpec:
    return PhraseSpec(key, category, kind, default, label, frozenset(placeholders), allowed_modes)


PHRASE_SPECS: dict[str, PhraseSpec] = {
    s.key: s
    for s in (
        _spec(
            "activity.welcome",
            "activity",
            "template",
            "Добро пожаловать, {name}. Осмотрись, правила почитай. ✂️👁🖤",
            label="Приветствие новичка",
            placeholders=("name",),
        ),
        _spec(
            "activity.farewell",
            "activity",
            "template",
            "{name} ушёл. Бывает.",
            label="Прощание с ушедшим",
            placeholders=("name",),
        ),
        _spec(
            "ai_chat.error_replies",
            "ai_chat",
            "list",
            [
                "Сегодня без разговоров. Не в настроении.",
                "Помолчим. Так тоже можно. 🖤",
            ],
            label="Ответы, когда ИИ не отвечает",
        ),
    )
}

PHRASE_KEYS: tuple[str, ...] = tuple(PHRASE_SPECS.keys())
# порядок категорий сохраняем (для вкладок панели)
PHRASE_CATEGORIES: tuple[str, ...] = tuple(
    dict.fromkeys(spec.category for spec in PHRASE_SPECS.values())
)
