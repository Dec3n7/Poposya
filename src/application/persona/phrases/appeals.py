"""Фразы персоны: апелляции наказаний."""

from src.application.persona.phrases._base import STATIC_MODES, _spec

SPECS = [
    # --- апелляции наказаний ---
    _spec(
        "appeals.button_label",
        "appeals",
        "str",
        "Обжаловать",
        label="Апелляции: подпись кнопки в ЛС",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "appeals.modal_title",
        "appeals",
        "str",
        "Обжалование наказания",
        label="Апелляции: заголовок формы",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "appeals.modal_field",
        "appeals",
        "str",
        "Почему наказание несправедливо?",
        label="Апелляции: подпись поля формы",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "appeals.submitted",
        "appeals",
        "str",
        "Апелляция отправлена — модераторы её посмотрят.",
        label="Апелляции: подтверждение отправки",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "appeals.duplicate",
        "appeals",
        "str",
        "Ты уже подал апелляцию — она ещё на рассмотрении.",
        label="Апелляции: уже есть открытая",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "appeals.empty",
        "appeals",
        "str",
        "Пустую апелляцию не приму — напиши, в чём дело.",
        label="Апелляции: пустой текст",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "appeals.closed",
        "appeals",
        "str",
        "Обжалование на этом сервере сейчас недоступно.",
        label="Апелляции: обжалование выключено",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "appeals.review_title",
        "appeals",
        "str",
        "Апелляция на наказание",
        label="Апелляции: заголовок карточки модератору",
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "appeals.approved_dm",
        "appeals",
        "template",
        "Твою апелляцию на «{guild}» приняли — наказание снято. Возвращайся аккуратнее.",
        label="Апелляции: ЛС при одобрении",
        placeholders=("guild",),
        allowed_modes=STATIC_MODES,
    ),
    _spec(
        "appeals.rejected_dm",
        "appeals",
        "template",
        "Апелляцию на «{guild}» отклонили — наказание остаётся в силе.",
        label="Апелляции: ЛС при отклонении",
        placeholders=("guild",),
        allowed_modes=STATIC_MODES,
    ),
]
