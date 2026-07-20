"""Каталог прав Discord для панели: курируемое подмножество прав с русскими
названиями, категориями и флагом «опасное» (панель требует подтверждения при
включении). Значения битов — стабильные публичные константы Discord.

Administrator (1<<3) НЕ входит в каталог как редактируемое право: панель его не
выдаёт и не снимает никогда — бит сохраняется как есть (см. command_executor
`_role_permissions`). Каталог — единый источник правды для API и фронта: список
отдаёт `GET /roles/permissions`, туда же кладём маску «что доступно самому боту».
"""

ADMINISTRATOR_BIT = 1 << 3

# (name, bit, label, dangerous). name дублирует discord.py-флаг — по нему бот
# ничего не ищет, он для отладки/аудита; управление идёт по битам.
_CATEGORIES: list[tuple[str, str, list[tuple[str, int, str, bool]]]] = [
    (
        "general",
        "Основные",
        [
            ("view_channel", 1 << 10, "Видеть каналы", False),
            ("send_messages", 1 << 11, "Писать сообщения", False),
            ("send_messages_in_threads", 1 << 38, "Писать в ветках", False),
            ("create_public_threads", 1 << 35, "Создавать ветки", False),
            ("read_message_history", 1 << 16, "История сообщений", False),
            ("add_reactions", 1 << 6, "Ставить реакции", False),
            ("embed_links", 1 << 14, "Ссылки-превью", False),
            ("attach_files", 1 << 15, "Прикреплять файлы", False),
            ("use_external_emojis", 1 << 18, "Внешние эмодзи", False),
            ("use_external_stickers", 1 << 37, "Внешние стикеры", False),
            ("use_application_commands", 1 << 31, "Слэш-команды", False),
            ("change_nickname", 1 << 26, "Менять свой ник", False),
            ("create_instant_invite", 1 << 0, "Создавать приглашения", False),
        ],
    ),
    (
        "voice",
        "Голос",
        [
            ("connect", 1 << 20, "Подключаться к голосу", False),
            ("speak", 1 << 21, "Говорить", False),
            ("stream", 1 << 9, "Стримить / видео", False),
            ("use_voice_activation", 1 << 25, "Активация по голосу", False),
            ("priority_speaker", 1 << 8, "Приоритетный динамик", False),
            ("request_to_speak", 1 << 32, "Просить слово (трибуна)", False),
        ],
    ),
    (
        "moderation",
        "Модерация",
        [
            ("kick_members", 1 << 1, "Кик участников", True),
            ("ban_members", 1 << 2, "Бан участников", True),
            ("moderate_members", 1 << 40, "Тайм-аут участников", True),
            ("manage_messages", 1 << 13, "Удалять чужие сообщения", True),
            ("mute_members", 1 << 22, "Мут в голосе", True),
            ("deafen_members", 1 << 23, "Оглушать в голосе", True),
            ("move_members", 1 << 24, "Перемещать по голосовым", True),
            ("mention_everyone", 1 << 17, "Пинговать @everyone", True),
            ("view_audit_log", 1 << 7, "Смотреть журнал аудита", False),
        ],
    ),
    (
        "management",
        "Управление",
        [
            ("manage_channels", 1 << 4, "Управлять каналами", True),
            ("manage_roles", 1 << 28, "Управлять ролями", True),
            ("manage_guild", 1 << 5, "Управлять сервером", True),
            ("manage_webhooks", 1 << 29, "Вебхуки", True),
            ("manage_nicknames", 1 << 27, "Менять чужие ники", True),
            ("manage_threads", 1 << 34, "Управлять ветками", True),
            ("manage_emojis", 1 << 30, "Эмодзи и стикеры", False),
            ("manage_events", 1 << 33, "Мероприятия", False),
        ],
    ),
]


def catalog_json() -> list[dict]:
    """Каталог для фронта: категории с правами; bit — строкой (битовое поле не
    влезает в JS-number)."""
    return [
        {
            "key": key,
            "label": label,
            "perms": [
                {"name": name, "bit": str(bit), "label": plabel, "dangerous": dangerous}
                for (name, bit, plabel, dangerous) in perms
            ],
        }
        for (key, label, perms) in _CATEGORIES
    ]


def all_catalog_bits() -> int:
    """OR всех битов каталога — маска «полного набора» (когда у бота Administrator,
    считаем доступным всё из каталога)."""
    mask = 0
    for _key, _label, perms in _CATEGORIES:
        for _name, bit, _plabel, _dangerous in perms:
            mask |= bit
    return mask
