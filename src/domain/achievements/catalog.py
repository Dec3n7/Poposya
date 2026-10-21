"""Каталог достижений (MVP). Все условия выводимы из текущего состояния —
значит открываются и ретроактивно (бэкфилл), и без единого нового счётчика.

Тиры — словарь редкостей (common→legendary), как у находок. Иконка ачивки —
ключ ассета: `render/icons/<icon>.svg` (собственный вектор, с фолбэком на
Twemoji для эмодзи-значений); карточка резолвит ключ при рендере."""

from __future__ import annotations

from src.domain.achievements.entities import Achievement, Tier


def _a(
    id: str,
    name: str,
    description: str,
    tier: Tier,
    icon: str,
    stat_label: str,
    stat,
    unlocked,
) -> Achievement:
    return Achievement(id, name, description, tier, icon, stat_label, stat, unlocked)


# Показатели-геттеры для будущей витрины (число + подпись); карточка B1 их не
# рисует, но домен хранит — пригодятся для /achievements-плиток.
_POINTS = (lambda s: s.points, "очков")
_DEEP = (lambda s: s.deep_dialogs, "глубоких диалогов")
_FINDS = (lambda s: s.finds_count, "находок")
_LIKES = (lambda s: s.likes_count, "любимых треков")
_VOICE = (lambda s: int(s.voice_hours), "часов в войсе")

CATALOG: list[Achievement] = [
    # ── отношения: ступени близости (по числу пройденных порогов ролей) ──
    _a(
        "rel_level_1",
        "Случайный прохожий",
        "Первый шаг к Попосе сделан.",
        Tier.COMMON,
        "rel_level_1",
        _POINTS[1],
        _POINTS[0],
        lambda s: s.level >= 1,
    ),
    _a(
        "rel_level_2",
        "Знакомый силуэт",
        "Попося начала тебя узнавать.",
        Tier.COMMON,
        "rel_level_2",
        _POINTS[1],
        _POINTS[0],
        lambda s: s.level >= 2,
    ),
    _a(
        "rel_level_3",
        "Занятный собеседник",
        "С тобой ей интересно.",
        Tier.UNCOMMON,
        "rel_level_3",
        _POINTS[1],
        _POINTS[0],
        lambda s: s.level >= 3,
    ),
    _a(
        "rel_level_4",
        "На одной волне",
        "Вы с Попосей на одной волне.",
        Tier.UNCOMMON,
        "rel_level_4",
        _POINTS[1],
        _POINTS[0],
        lambda s: s.level >= 4,
    ),
    _a(
        "rel_level_5",
        "Вечерняя компания",
        "Ты — часть её вечеров.",
        Tier.RARE,
        "rel_level_5",
        _POINTS[1],
        _POINTS[0],
        lambda s: s.level >= 5,
    ),
    _a(
        "rel_level_6",
        "Особенный",
        "Попося дорожит тобой особенно.",
        Tier.RARE,
        "rel_level_6",
        _POINTS[1],
        _POINTS[0],
        lambda s: s.level >= 6,
    ),
    _a(
        "rel_exclusive",
        "Единственный",
        "Единственный на сервере — у самого её сердца.",
        Tier.LEGENDARY,
        "rel_exclusive",
        _POINTS[1],
        _POINTS[0],
        lambda s: s.is_exclusive,
    ),
    _a(
        "rel_survey",
        "Открытая душа",
        "Ты рассказал Попосе о себе.",
        Tier.COMMON,
        "rel_survey",
        _POINTS[1],
        _POINTS[0],
        lambda s: s.survey_completed,
    ),
    _a(
        "rel_deep_5",
        "По душам",
        "Пять долгих разговоров с Попосей.",
        Tier.UNCOMMON,
        "rel_deep_5",
        _DEEP[1],
        _DEEP[0],
        lambda s: s.deep_dialogs >= 5,
    ),
    _a(
        "rel_deep_25",
        "Родственная душа",
        "Двадцать пять глубоких диалогов.",
        Tier.RARE,
        "rel_deep_25",
        _DEEP[1],
        _DEEP[0],
        lambda s: s.deep_dialogs >= 25,
    ),
    # ── находки ──
    _a(
        "finds_first",
        "Первая находка",
        "Твоя первая ночная находка.",
        Tier.COMMON,
        "finds_first",
        _FINDS[1],
        _FINDS[0],
        lambda s: s.finds_count >= 1,
    ),
    _a(
        "finds_10",
        "Ночной охотник",
        "Десять находок в коллекции.",
        Tier.UNCOMMON,
        "finds_10",
        _FINDS[1],
        _FINDS[0],
        lambda s: s.finds_count >= 10,
    ),
    _a(
        "finds_50",
        "Коллекционер",
        "Полсотни находок — впечатляет.",
        Tier.RARE,
        "finds_50",
        _FINDS[1],
        _FINDS[0],
        lambda s: s.finds_count >= 50,
    ),
    _a(
        "finds_legendary",
        "Редкая удача",
        "Тебе попалась легендарная находка.",
        Tier.RARE,
        "finds_legendary",
        _FINDS[1],
        _FINDS[0],
        lambda s: s.has_legendary_find,
    ),
    # ── музыка (по лайкам — единственный трекаемый per-user сигнал) ──
    _a(
        "music_likes_50",
        "Меломан",
        "Полсотни треков в личной коллекции.",
        Tier.UNCOMMON,
        "music_likes_50",
        _LIKES[1],
        _LIKES[0],
        lambda s: s.likes_count >= 50,
    ),
    _a(
        "music_likes_250",
        "Музыкальный гурман",
        "Двести пятьдесят любимых треков.",
        Tier.RARE,
        "music_likes_250",
        _LIKES[1],
        _LIKES[0],
        lambda s: s.likes_count >= 250,
    ),
    # ── голосовые каналы ──
    _a(
        "voice_50",
        "Завсегдатай войса",
        "Пятьдесят часов в голосовых.",
        Tier.UNCOMMON,
        "voice_50",
        _VOICE[1],
        _VOICE[0],
        lambda s: s.voice_hours >= 50,
    ),
    _a(
        "voice_100",
        "Голос сервера",
        "Сто часов в голосовых.",
        Tier.RARE,
        "voice_100",
        _VOICE[1],
        _VOICE[0],
        lambda s: s.voice_hours >= 100,
    ),
    _a(
        "voice_500",
        "Житель войса",
        "Пятьсот часов в голосовых — легенда.",
        Tier.LEGENDARY,
        "voice_500",
        _VOICE[1],
        _VOICE[0],
        lambda s: s.voice_hours >= 500,
    ),
]

BY_ID: dict[str, Achievement] = {a.id: a for a in CATALOG}
