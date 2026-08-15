"""Фразы персоны: ачивки."""

from src.application.persona.phrases._base import STATIC_MODES, _spec

SPECS = [
    _spec(
        "achievements.unlocked_announce",
        "achievements",
        "template",
        "{user_mention} открыл достижение — «{name}»! ✨",
        label="Ачивки: объявление об открытии",
        placeholders=("user_mention", "name"),
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "achievements.showcase_title",
        "achievements",
        "template",
        "Достижения — {unlocked}/{total}",
        label="Ачивки: заголовок витрины",
        placeholders=("unlocked", "total"),
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "achievements.showcase_empty",
        "achievements",
        "str",
        "Пока ни одного достижения — но всё впереди.",
        label="Ачивки: витрина пуста",
        allowed_modes=STATIC_MODES,
    ),
]
