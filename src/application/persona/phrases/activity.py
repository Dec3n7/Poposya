"""Фразы персоны: приветствия/прощания, возвращение, альбом, календарь, скука и мысли."""

from src.application.persona.phrases._base import AI_ONLY, STATIC_MODES, _spec

SPECS = [
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
        "Участник {name} покинул сервер. Попрощайся одной фразой в своём стиле — сухо, без драмы.",
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
        "Сегодня {holiday}. Объяви об этом серверу в своём стиле — празднично, но без сюсюканья.",
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
]
