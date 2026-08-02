"""Готовые наборы ролей С ПРАВАМИ («пресеты команды сервера»).

В отличие от косметических шаблонов (frontend `roleTemplates.ts`, создаются без
прав), эти роли приезжают уже настроенными. Панель шлёт боту только КЛЮЧ пресета
— состав и права берутся здесь, на сервере, а не из тела запроса. Значит клиент
не может подсунуть произвольные биты: он лишь выбирает курируемый набор.

Настоящая граница всё равно в боте: `command_executor._role_preset` ещё раз
зажимает маску под права самого бота и никогда не выдаёт Administrator. Здесь —
только политика (какой роли что положено).

Права описываем ИМЕНАМИ прав каталога (`permissions_catalog`), маску собираем из
них — так набор читается глазами и остаётся в одном источнике правды с редактором
прав. Мод-лестница кумулятивна: каждая ступень включает права предыдущей.
"""

from src.api.permissions_catalog import PERM_BITS, PERM_LABELS

# --- наборы прав по ступеням (кумулятивно) ---

# стажёр: остудить конфликт и почистить спам, ничего необратимого
_TRIAL = ("moderate_members", "manage_messages", "mute_members", "deafen_members")
# модератор: полный фронт — кик/бан, ники, голос, аудит-лог
_MOD = (
    *_TRIAL,
    "kick_members",
    "ban_members",
    "manage_nicknames",
    "move_members",
    "view_audit_log",
)
# старший: формирует пространство — роли (ниже себя) и ветки
_SENIOR = (*_MOD, "manage_roles", "manage_threads")
# куратор: почти-админ без самого Administrator
_CURATOR = (*_SENIOR, "manage_channels", "manage_guild", "manage_webhooks")


def _c(hex_: str) -> int:
    return int(hex_.lstrip("#"), 16)


def _mask(names: tuple[str, ...]) -> int:
    mask = 0
    for name in names:
        mask |= PERM_BITS[name]
    return mask


def _role(
    name: str,
    color: str | None,
    perms: tuple[str, ...],
    *,
    hoist: bool = False,
    mentionable: bool = False,
) -> dict:
    return {
        "name": name,
        "color": _c(color) if color else None,
        "hoist": hoist,
        "mentionable": mentionable,
        "perms": perms,
    }


# Имена ролей несут эмодзи прямо в тексте (работает на любом сервере, без буста).
# Это лишь стартовые значения — их легко переименовать в Дискорде/панели.
PRESETS: list[dict] = [
    {
        "key": "mod_ladder",
        "name": "Модерация — лестница",
        "description": (
            "Четыре ступени персонала по нарастающей: стажёр → модератор → "
            "старший → куратор. Administrator не выдаём. После создания подними "
            "роли выше участников, которыми они управляют."
        ),
        "roles": [
            _role("🕯️ Стажёр", "#e6b24d", _TRIAL, hoist=True),
            _role("🏮 Модератор", "#ff8a5c", _MOD, hoist=True),
            _role("🔦 Старший модератор", "#e05c6a", _SENIOR, hoist=True),
            _role("🌙 Куратор", "#b57be0", _CURATOR, hoist=True),
        ],
    },
    {
        "key": "content_hosts",
        "name": "Ведущие — голоса сервера",
        "description": (
            "Роли для тех, кто ведёт активности: киноклуб, анонсы, ивенты. "
            "Серверные права минимальные (мероприятия, ветки) — постить и "
            "пинить в своём канале даётся доступом на уровне канала."
        ),
        "roles": [
            _role("🎞️ Ведущий киноклуба", "#6ab0d8", ("manage_events", "create_public_threads")),
            _role("📢 Глашатай", "#57c4b6", ("create_public_threads",)),
            _role("🎉 Затейник", "#f0899f", ("manage_events", "create_public_threads")),
        ],
    },
    {
        "key": "audience",
        "name": "Аудитории — кого пинговать",
        "description": (
            "Опциональные роли-аудитории без прав, УПОМИНАЕМЫЕ: ведущие пингуют "
            "их для анонсов без права «Пинговать @everyone». Раздачу сделай "
            "самообслуживанием — свяжи интерес анкеты /introduce с ролью в "
            "«Роли по интересам», и участники берут её сами."
        ),
        "roles": [
            _role("🍿 Киноман", "#e6b24d", (), mentionable=True),
            _role("🎮 Игрок", "#6ab0d8", (), mentionable=True),
            _role("🎵 Меломан", "#b57be0", (), mentionable=True),
            _role("📰 Новости", "#57c4b6", (), mentionable=True),
        ],
    },
    {
        "key": "pronouns",
        "name": "Местоимения",
        "description": (
            "Инклюзивные роли-местоимения без прав, не упоминаемые. Самовыдача: "
            "свяжи их с интересами анкеты /introduce в «Роли по интересам» или "
            "выдавай вручную."
        ),
        "roles": [
            _role("он / его", "#6ab0d8", ()),
            _role("она / её", "#f0899f", ()),
            _role("они / их", "#57c46a", ()),
            _role("спроси", "#99aab5", ()),
        ],
    },
    {
        "key": "notifications",
        "name": "Уведомления и пинги",
        "description": (
            "Роли-подписки без прав, УПОМИНАЕМЫЕ: подписался — пингуют только по "
            "теме. Раздача — самообслуживанием через «Роли по интересам»."
        ),
        "roles": [
            _role("🔔 Ивенты", "#e6b24d", (), mentionable=True),
            _role("📣 Апдейты", "#6ab0d8", (), mentionable=True),
            _role("🎁 Розыгрыши", "#f0899f", (), mentionable=True),
            _role("🎬 Киновечер", "#b57be0", (), mentionable=True),
        ],
    },
    {
        "key": "platforms",
        "name": "Игровые платформы",
        "description": (
            "Роли-платформы без прав — удобно собирать компании по железу. "
            "Самовыдача через «Роли по интересам» или вручную."
        ),
        "roles": [
            _role("💻 ПК", "#99aab5", ()),
            _role("🎮 PlayStation", "#6ab0d8", ()),
            _role("❎ Xbox", "#57c46a", ()),
            _role("🔴 Switch", "#e05c6a", ()),
            _role("📱 Mobile", "#57c4b6", ()),
        ],
    },
    {
        "key": "media",
        "name": "Медиа и оформление",
        "description": (
            "Функциональные роли для оформителей, не мод-власть. Оформитель "
            "управляет эмодзи и стикерами; Куратор закрепов — Manage Messages "
            "(серверно: сможет и чистить чужие сообщения; при желании сузь право "
            "по нужному каналу)."
        ),
        "roles": [
            _role("🎨 Оформитель", "#f0899f", ("manage_emojis",)),
            _role("📌 Куратор закрепов", "#e6b24d", ("manage_messages",)),
        ],
    },
]


def _role_json(role: dict) -> dict:
    return {
        "name": role["name"],
        "color": role["color"],
        "hoist": role["hoist"],
        "mentionable": role["mentionable"],
        "permissions": str(_mask(role["perms"])),  # строкой: битовое поле не влезает в JS-number
        "perm_labels": [PERM_LABELS[name] for name in role["perms"]],
    }


def presets_json() -> list[dict]:
    """Пресеты для панели: состав ролей с русскими метками прав (для чипов/тултипа)."""
    return [
        {
            "key": preset["key"],
            "name": preset["name"],
            "description": preset["description"],
            "roles": [_role_json(r) for r in preset["roles"]],
        }
        for preset in PRESETS
    ]


def get_preset(key: str) -> dict | None:
    return next((p for p in PRESETS if p["key"] == key), None)


def preset_payload(preset: dict) -> dict:
    """Тело команды role.preset: маску кладём строкой (как везде для битовых полей)."""
    return {
        "roles": [
            {
                "name": r["name"],
                "color": r["color"],
                "hoist": r["hoist"],
                "mentionable": r["mentionable"],
                "permissions": str(_mask(r["perms"])),
            }
            for r in preset["roles"]
        ]
    }
