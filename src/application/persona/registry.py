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
        # --- находки: анонс ---
        _spec(
            "finds.opener",
            "finds",
            "list",
            [
                "Я сегодня гуляла под мелким дождём около {place}. Заметила кое-что… интересное.",
                "Опять бродила ночью. Нашла кое-что, что может тебе понравиться.",
                "Иногда город сам подкидывает подарки. Сегодня был такой момент.",
                "Не удержалась. В старом районе кое-что блеснуло под фонарём.",
            ],
            label="Анонс находки: зачин (случайный)",
            placeholders=("place",),
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.announce_title",
            "finds",
            "str",
            "🌙 Ночная находка",
            label="Анонс находки: заголовок",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.announce_body",
            "finds",
            "str",
            "Не обещаю, что оно всё ещё там — Токио быстрый. "
            "Но если хочешь, можешь сходить посмотреть.",
            label="Анонс находки: приглашение",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.announce_place",
            "finds",
            "template",
            "**Место:** {place}\n-# {place_flavor}",
            label="Анонс находки: строка места",
            placeholders=("place", "place_flavor"),
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.announce_footer",
            "finds",
            "str",
            "Одна попытка на находку. Заберёт первый, кому повезёт.",
            label="Анонс находки: футер",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.expired",
            "finds",
            "str",
            "-# 🌙 Опоздали. Токио быстрый — находки не ждут.",
            label="Приписка к протухшей находке",
            allowed_modes=STATIC_MODES,
        ),
        # --- находки: поход по кнопке ---
        _spec(
            "finds.claim_gone",
            "finds",
            "str",
            "Там уже пусто. Или кто-то успел раньше, или Токио забрал своё.",
            label="Поход: находку уже забрали",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.claim_already",
            "finds",
            "str",
            "Ты уже ходил к этой находке. Второго шанса город не даёт.",
            label="Поход: повторная попытка",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.claim_cooldown",
            "finds",
            "template",
            "Ноги ещё гудят после прошлого похода. Возвращайся {retry}.",
            label="Поход: кулдаун",
            placeholders=("retry",),
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.claim_fail",
            "finds",
            "list",
            [
                "Пусто? Как неожиданно. Хотя… я же не говорила, что оно будет лежать "
                "и ждать именно тебя.",
                "Ничего? Жаль. В следующий раз смотри внимательнее.",
                "Пф. Пустые руки. Не расстраивайся, я тоже иногда возвращаюсь ни с чем. "
                "Хотя и реже.",
            ],
            label="Поход: неудача (случайная)",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.points_tail",
            "finds",
            "template",
            "-# {delta} очков. Сейчас у тебя {total}.",
            label="Поход: приписка об очках при неудаче",
            placeholders=("delta", "total"),
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.claim_success_note",
            "finds",
            "template",
            "{item_emoji} **{item}** теперь в твоей коллекции (`/collection`). +{delta} очков.",
            label="Поход: личное подтверждение успеха",
            placeholders=("item_emoji", "item", "delta"),
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.claim_taken_note",
            "finds",
            "template",
            "-# ✅ Забрал {user_mention} — {item_emoji} {item}",
            label="Поход: приписка к закрытому анонсу",
            placeholders=("user_mention", "item_emoji", "item"),
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.success_low",
            "finds",
            "str",
            "Хорошо. Ты его действительно забрал. Не ожидала.",
            label="Реакция на успех: низкий уровень отношений",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.success_mid",
            "finds",
            "str",
            "Неплохо. Это даже мило с твоей стороны.",
            label="Реакция на успех: средний уровень",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.success_high",
            "finds",
            "str",
            "…Ты правда пошёл туда ради этого? Хм. Спасибо. ✂️👁🖤",
            label="Реакция на успех: высокий уровень",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.success_legendary",
            "finds",
            "str",
            "…Стой. Ты нашёл ЭТО? Я запомню этот момент. Надолго. ✂️👁🖤",
            label="Реакция на успех: легендарная находка",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.claim_award",
            "finds",
            "template",
            "{user_mention} получает **+{delta}** очков ({rarity_emoji} {rarity} находка).",
            label="Публичное объявление успеха: строка очков",
            placeholders=("user_mention", "delta", "rarity_emoji", "rarity"),
            allowed_modes=STATIC_MODES,
        ),
        # --- находки: /finds, /collection, /gift, /walk ---
        _spec(
            "finds.none_active",
            "finds",
            "str",
            "Сейчас находок нет. Я хожу гулять, когда сама захочу.",
            label="/finds: активных находок нет",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.active_title",
            "finds",
            "str",
            "🌙 Активная находка",
            label="/finds: заголовок карточки",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.active_body",
            "finds",
            "template",
            "**Место:** {place}\n-# {place_flavor}\n\nПропадёт {expires}.",
            label="/finds: тело карточки",
            placeholders=("place", "place_flavor", "expires"),
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.active_jump",
            "finds",
            "template",
            "[К анонсу]({url})",
            label="/finds: подпись ссылки на анонс",
            placeholders=("url",),
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.collection_empty",
            "finds",
            "str",
            "Пока пусто. Следи за моими ночными прогулками — и успевай первым.",
            label="/collection: пустая коллекция",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.collection_title",
            "finds",
            "template",
            "🗃️ Коллекция ({count})",
            label="/collection: заголовок",
            placeholders=("count",),
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.collection_gifted_mark",
            "finds",
            "str",
            " · 🎁 подарено мне",
            label="/collection: пометка подаренного",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.gift_no_item",
            "finds",
            "str",
            "У тебя нет такого предмета. Дарить чужое — не твой уровень.",
            label="/gift: предмета нет",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.gift",
            "finds",
            "dict",
            {
                "common": "Ты серьёзно решил мне это подарить? …Ладно. Я приму. И запомню.",
                "uncommon": "Хм. У тебя неплохой вкус. Приму. И да — я запомню.",
                "rare": "…Это правда мне? Такое я не забываю. Спасибо. 🖤",
                "legendary": "…Я не знаю, что сказать. Это со мной навсегда. "
                "Как и этот момент. ✂️👁🖤",
            },
            label="/gift: реакция по редкости (common/uncommon/rare/legendary)",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.gift_award",
            "finds",
            "template",
            "{user_mention} получает **+{bonus}** очков.",
            label="/gift: строка очков",
            placeholders=("user_mention", "bonus"),
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.walk_cooldown",
            "finds",
            "template",
            "Я тебе не такси. Следующая прогулка — {retry}.",
            label="/walk: кулдаун",
            placeholders=("retry",),
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.walk_poor",
            "finds",
            "template",
            "Прогулка стоит **{cost}** очков, у тебя {total}. Сначала заслужи.",
            label="/walk: не хватает очков",
            placeholders=("cost", "total"),
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.walk_fail",
            "finds",
            "list",
            [
                "Прогулялась впустую. Город сегодня жадный. Бывает.",
                "Ничего. Даже кот куда-то делся. Не мой вечер — и не твой.",
            ],
            label="/walk: неудача (случайная)",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.walk_fail_tail",
            "finds",
            "template",
            "-# −{cost} очков за прогулку. Сейчас у тебя {total}.",
            label="/walk: приписка об очках при неудаче",
            placeholders=("cost", "total"),
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.walk_success",
            "finds",
            "list",
            [
                "Прогулялась. Дождь, фонари, один подозрительный кот. И вот — держи.",
                "Сходила. Ради тебя, между прочим. Смотри, что принесла.",
            ],
            label="/walk: успех (случайный)",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.walk_success_tail",
            "finds",
            "template",
            "{item_emoji} **{item}** — в твоей коллекции.\n"
            "-# Итог: {sign}{delta} очков (прогулка −{cost}, находка сверху). "
            "Сейчас у тебя {total}.",
            label="/walk: итог успеха",
            placeholders=("item_emoji", "item", "sign", "delta", "cost", "total"),
            allowed_modes=STATIC_MODES,
        ),
        # --- находки: админ-команда спавна ---
        _spec(
            "finds.admin_no_channel",
            "finds",
            "str",
            "Не нашла канал для находок. Задай его: "
            "`/config set finds_channel_id #канал` — или создай канал «основной».",
            label="/spawnfind: канал не найден",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.admin_active_exists",
            "finds",
            "str",
            "Активная находка уже висит — сначала заберите её.",
            label="/spawnfind: находка уже есть",
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.admin_spawned",
            "finds",
            "template",
            "🌙 Находка заспавнена в {channel_mention}.",
            label="/spawnfind: успех",
            placeholders=("channel_mention",),
            allowed_modes=STATIC_MODES,
        ),
        _spec(
            "finds.admin_spawn_failed",
            "finds",
            "str",
            "Не вышло заспавнить — глянь логи бота.",
            label="/spawnfind: не вышло",
            allowed_modes=STATIC_MODES,
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
