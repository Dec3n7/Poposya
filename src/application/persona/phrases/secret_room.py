"""Фразы персоны: секретная комната."""

from src.application.persona.phrases._base import STATIC_MODES, _spec

SPECS = [
    # --- тайная комната ---
    _spec(
        "secret_room.dm",
        "secret_room",
        "template",
        "Ты дошёл туда, куда доходят немногие. Держи ключ: **`{code}`**\n\n"
        "Введи `/secret` с этим ключом на сервере — и на {hours} часов откроется "
        "место, о котором не пишут в правилах. Дверь увидят только те, кто "
        "заслужил столько же, сколько ты.\n\n"
        "Не разбрасывайся. Второго я не дам. ✂️👁🖤",
        label="ЛС с ключом при достижении уровня",
        placeholders=("code", "hours"),
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "secret_room.room_welcome",
        "secret_room",
        "template",
        "Дверь открыта. У вас есть время до <t:{ts}:t> — потом я всё здесь сотру, "
        "и этого разговора не было.\n\n"
        "Кто видит этот канал — тот свой. Ведите себя соответственно. ✂️👁🖤",
        label="Первое сообщение в открытой комнате",
        placeholders=("ts",),
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "secret_room.no_rooms",
        "secret_room",
        "str",
        "Не понимаю, о чём ты. Здесь нет никаких тайных комнат.",
        label="/secret: уровень не достигнут",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "secret_room.key_issued",
        "secret_room",
        "template",
        "Твой ключ: **`{code}`**. Не разбрасывайся.",
        label="/secret: ключ довыдан",
        placeholders=("code",),
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "secret_room.key_used_own",
        "secret_room",
        "str",
        "Свой ключ ты уже использовал. Дверь открывается один раз.",
        label="/secret: свой ключ уже использован",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "secret_room.key_show",
        "secret_room",
        "template",
        "Твой ключ: **`{code}`**. Введи его аргументом, когда решишься.",
        label="/secret: показать свой ключ",
        placeholders=("code",),
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "secret_room.room_active",
        "secret_room",
        "template",
        "Комната уже открыта — {channel_mention}. Побереги свой ключ.",
        label="/secret: комната уже открыта",
        placeholders=("channel_mention",),
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "secret_room.no_code",
        "secret_room",
        "str",
        "У тебя нет ключа. Ключи я раздаю сама — и не всем.",
        label="/secret: ключа нет",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "secret_room.used",
        "secret_room",
        "str",
        "Этот ключ уже сгорел. Дверь открывается один раз.",
        label="/secret: ключ сгорел",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "secret_room.wrong",
        "secret_room",
        "str",
        "Неверный ключ. Я бы на твоём месте не подбирала.",
        label="/secret: неверный ключ",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "secret_room.fallback_no",
        "secret_room",
        "str",
        "Нет.",
        label="/secret: отказ без причины",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "secret_room.no_permission",
        "secret_room",
        "str",
        "Не хватает права Manage Channels — дверь не открылась.",
        label="/secret: нет прав на каналы",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "secret_room.opened",
        "secret_room",
        "template",
        "Дверь открыта: {channel_mention}. Ключ сгорел — так и задумано.",
        label="/secret: дверь открыта",
        placeholders=("channel_mention",),
        allowed_modes=STATIC_MODES,
    ),
]
