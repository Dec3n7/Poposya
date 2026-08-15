"""Базовые примитивы каталога фраз: режимы блока, описание ключа (PhraseSpec) и
конструктор _spec. Вынесены отдельно, чтобы модули phrases/<category>.py строили
свои списки SPECS без циклического импорта через registry."""

from dataclasses import dataclass

# --- режимы блока (mode персона-фразы) ---
# Дефолтный режим ключа = allowed_modes[0]. У AI-блоков это ai_then_static,
# у статик-ключей (подписи/шаблоны без AI) - static, у AI-инструкций (*.ai)
# режим один и не выбирается (правится сам текст инструкции).
ALL_MODES: tuple[str, ...] = ("ai_then_static", "static", "silent")
STATIC_MODES: tuple[str, ...] = ("static", "silent")
AI_ONLY: tuple[str, ...] = ("ai_then_static",)
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
