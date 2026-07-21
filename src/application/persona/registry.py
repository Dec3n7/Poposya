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
    "accent_color": 0x9B59B6,  # accent эмбедов (см. infrastructure/discord/accent.py)
    "presence": [],  # строки Discord-присутствия; пусто = встроенный канон Попоси
}

# лимиты атрибутов (валидация set_identity; панель ограничивает те же значения)
IDENTITY_TEXT_MAX = 64  # display_name / signature
PRESENCE_LINE_MAX = 128  # одна строка статуса (лимит Discord)
PRESENCE_LINES_MAX = 20

# --- режимы блока (mode персона-фразы) ---
# Дефолтный режим ключа = allowed_modes[0]. У AI-блоков это ai_then_static,
# у статик-ключей (подписи/шаблоны без AI) — static, у AI-инструкций (*.ai)
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


PHRASE_SPECS: dict[str, PhraseSpec] = {
    s.key: s
    for s in (
        # --- активность: приветствия/прощания ---
        _spec(
            "activity.welcome",
            "activity",
            "template",
            "Добро пожаловать, {name}. Осмотрись, правила почитай. ✂️👁🖤",
            label="Приветствие новичка (статика)",
            placeholders=("name",),
        ),
        _spec(
            "activity.welcome.ai",
            "activity",
            "template",
            "На сервер пришёл новый участник — {name}. "
            "Поприветствуй его в своём стиле: сдержанно, с лёгкой иронией, без сюсюканья.",
            label="Приветствие новичка (AI-инструкция)",
            placeholders=("name",),
            allowed_modes=AI_ONLY,
        ),
        _spec(
            "activity.farewell",
            "activity",
            "template",
            "{name} ушёл. Бывает.",
            label="Прощание с ушедшим (статика)",
            placeholders=("name",),
        ),
        _spec(
            "activity.farewell.ai",
            "activity",
            "template",
            "Участник {name} покинул сервер. "
            "Попрощайся одной фразой в своём стиле — сухо, без драмы.",
            label="Прощание с ушедшим (AI-инструкция)",
            placeholders=("name",),
            allowed_modes=AI_ONLY,
        ),
        # --- активность: возвращение после отсутствия (только AI; пустая
        # статика = молчать при сбое генерации, как и раньше) ---
        _spec(
            "activity.return",
            "activity",
            "template",
            "",
            label="Возвращение участника (статика; пусто = молчать)",
            placeholders=("name", "days"),
        ),
        _spec(
            "activity.return.ai",
            "activity",
            "template",
            "Участник {name} впервые написал после {days} дней отсутствия. "
            "Отметь его возвращение одной фразой в своём стиле: заметила, но без сцен.",
            label="Возвращение участника (AI-инструкция)",
            placeholders=("name", "days"),
            allowed_modes=AI_ONLY,
        ),
        # --- активность: альбом ---
        _spec(
            "activity.album",
            "activity",
            "list",
            [
                "В коллекцию. ✂️👁🖤",
                "Это стоило сохранить.",
                "Экспонат. Не благодарите.",
                "Редкий момент, когда вы меня не разочаровали.",
            ],
            label="Подпись экспоната альбома (статика, случайная)",
        ),
        _spec(
            "activity.album.ai",
            "activity",
            "template",
            "Сообщение участника {name} набрало {reactions} реакций и попадает в твой "
            "«Альбом» — коллекцию лучших моментов сервера. Текст сообщения: «{content}». "
            "Подпиши экспонат одной фразой в своём кураторском стиле.",
            label="Подпись экспоната альбома (AI-инструкция)",
            placeholders=("name", "reactions", "content"),
            allowed_modes=AI_ONLY,
        ),
        _spec(
            "activity.album_empty",
            "activity",
            "str",
            "*(без текста)*",
            label="Альбом: заглушка сообщения без текста",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "activity.album_link",
            "activity",
            "template",
            "[Перейти к сообщению]({url})",
            label="Альбом: подпись ссылки на оригинал",
            placeholders=("url",),
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "activity.album_footer",
            "activity",
            "template",
            "{reactions} реакций • #{channel}",
            label="Альбом: подпись-футер экспоната",
            placeholders=("reactions", "channel"),
            allowed_modes=STATIC_MODES,
        ),
        # --- активность: календарь (праздники и ДР) ---
        _spec(
            "activity.holiday",
            "activity",
            "template",
            "Сегодня {holiday}. Так и быть — сегодня я добрее обычного. ✂️👁🖤",
            label="Объявление праздника (статика)",
            placeholders=("holiday",),
        ),
        _spec(
            "activity.holiday.ai",
            "activity",
            "template",
            "Сегодня {holiday}. Объяви об этом серверу в своём стиле — "
            "празднично, но без сюсюканья.",
            label="Объявление праздника (AI-инструкция)",
            placeholders=("holiday",),
            allowed_modes=AI_ONLY,
        ),
        _spec(
            "activity.holiday_bonus",
            "activity",
            "template",
            "-# 🎉 Весь день очки идут ×{multiplier}",
            label="Праздник: строка про множитель очков",
            placeholders=("multiplier",),
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "activity.birthday_remind",
            "activity",
            "template",
            "🎂 Через {days} дня — день рождения {user_mention}. Готовьтесь. Я — уже.",
            label="Напоминание о дне рождения",
            placeholders=("days", "user_mention"),
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "activity.birthday",
            "activity",
            "template",
            "с днём рождения. Сегодня можешь даже немного поныть. Один раз.",
            label="Поздравление с ДР (статика; «🎂 @имя — » добавляется всегда)",
            placeholders=("name",),
        ),
        _spec(
            "activity.birthday.ai",
            "activity",
            "template",
            "Сегодня день рождения у участника {name}. Поздравь его в своём "
            "стиле — тепло, но без пафоса и открыточных штампов.",
            label="Поздравление с ДР (AI-инструкция)",
            placeholders=("name",),
            allowed_modes=AI_ONLY,
        ),
        # --- активность: скука и случайные мысли (только AI) ---
        _spec(
            "activity.lonely",
            "activity",
            "template",
            "",
            label="Реплика в тихий канал (статика; пусто = молчать)",
            placeholders=("hours",),
        ),
        _spec(
            "activity.lonely.ai",
            "activity",
            "template",
            "В канале уже больше {hours} часов никто не пишет. "
            "Напиши одну реплику в пустоту в своём стиле — тебе слегка не хватает "
            "этих людей, но признаваться в этом прямо ты не станешь.",
            label="Реплика в тихий канал (AI-инструкция)",
            placeholders=("hours",),
            allowed_modes=AI_ONLY,
        ),
        _spec(
            "activity.random_thought",
            "activity",
            "template",
            "",
            label="Случайная мысль (статика; пусто = молчать)",
        ),
        _spec(
            "activity.random_thought.ai",
            "activity",
            "template",
            "Напиши одну случайную мысль или наблюдение в своём характере — "
            "про дождь, кофе, работу над артом, игры, Токио. Как будто просто "
            "захотелось сказать вслух. Без обращения к кому-то конкретному.",
            label="Случайная мысль (AI-инструкция)",
            allowed_modes=AI_ONLY,
        ),
        # --- ai_chat ---
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
