"""Фразы персоны: ошибки команд (глобальная сеть безопасности)."""

from src.application.persona.phrases._base import STATIC_MODES, _spec

SPECS = [
    # --- ошибки команд (глобальная сеть безопасности) ---
    _spec(
        "errors.cooldown",
        "errors",
        "template",
        "Слишком часто. Попробуй через {seconds} с.",
        label="Ошибка: кулдаун команды",
        placeholders=("seconds",),
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "errors.missing_perms",
        "errors",
        "str",
        "У тебя нет прав на эту команду.",
        label="Ошибка: у тебя нет прав",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "errors.bot_missing_perms",
        "errors",
        "str",
        "Мне не хватает прав в этом канале, чтобы выполнить команду.",
        label="Ошибка: боту не хватает прав",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "errors.no_dm",
        "errors",
        "str",
        "Эта команда работает только на сервере.",
        label="Ошибка: только на сервере",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "errors.check_failure",
        "errors",
        "str",
        "Команда сейчас недоступна.",
        label="Ошибка: команда недоступна",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "errors.internal",
        "errors",
        "template",
        "Ой, что-то сломалось на моей стороне. Я записала ошибку — "
        "назови код `{code}`, если повторится.",
        label="Ошибка: внутренний сбой (с кодом)",
        placeholders=("code",),
        allowed_modes=STATIC_MODES,
    ),
]
