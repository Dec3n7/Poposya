"""Форматирование недельного дайджеста: тон от статистики, факты для AI-промпта
и шаблонный фолбэк без ИИ. Работает с DigestView — срезом, где id участников уже
заменены на имена (это делает ког). Чистые функции: тестируются без Discord."""

from dataclasses import dataclass
from datetime import date

_WEEKDAYS_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


def weekday_name(day: date) -> str:
    return _WEEKDAYS_RU[day.weekday()]


@dataclass(frozen=True)
class DigestLine:
    """Строка участника с уже подставленным именем."""

    name: str
    detail: str  # «1200 очков», «42 находки»


@dataclass(frozen=True)
class DigestView:
    """Недельный срез с именами — готов к рендеру (AI-факты или шаблон)."""

    week_start: date
    week_end: date
    messages: int
    messages_delta: int  # к прошлой неделе
    voice_hours: int  # округлённые часы
    voice_delta: int
    members_delta: int
    peak_day_name: str  # "" если данных нет
    peak_day_messages: int
    stars: tuple[DigestLine, ...]
    birthdays: tuple[tuple[str, int], ...]  # (имя, через сколько дней)
    top_collector: DigestLine | None
    watched_titles: tuple[str, ...]


# --- тон от статистики: настроение подстраивается под неделю ---

# ключ -> подсказка тона для {tone} в промпте digest.instruction
TONE_HINTS: dict[str, str] = {
    "welcoming": (
        "На сервер влилось заметно людей — тон радушный и тёплый, порадуйся новеньким "
        "и позови их обжиться."
    ),
    "festive": "На неделе несколько дней рождения — тон лёгкий и праздничный.",
    "lively": (
        "Неделя выдалась бурной, движа было много — пиши бодро, с тихой гордостью за своих."
    ),
    "quiet": (
        "Неделя тихая и малолюдная — тон задумчивый и мягкий, без упрёка, чуть подбадривающий."
    ),
    "cozy": "Неделя ровная и спокойная — тон тёплый, уютный, по-домашнему.",
}


def digest_tone(view: DigestView) -> str:
    """Ключ настроения из чисел недели (порядок проверок = приоритет)."""
    if view.members_delta >= 3:
        return "welcoming"
    if len(view.birthdays) >= 2:
        return "festive"
    if view.messages and view.messages_delta > max(10, int(view.messages * 0.15)):
        return "lively"
    if view.messages < 20 or (view.messages and view.messages_delta < -int(view.messages * 0.25)):
        return "quiet"
    return "cozy"


# --- вспомогательное ---


def _delta(n: int, unit: str = "") -> str:
    if n > 0:
        return f" (+{n}{unit} к прошлой)"
    if n < 0:
        return f" ({n}{unit} к прошлой)"
    return ""


def _in_days(n: int) -> str:
    if n == 0:
        return "сегодня"
    if n == 1:
        return "завтра"
    return f"через {n} дн."


def _people(lines: tuple[DigestLine, ...]) -> str:
    return ", ".join(f"{ln.name} ({ln.detail})" for ln in lines)


def _birthdays(bdays: tuple[tuple[str, int], ...]) -> str:
    return ", ".join(f"{name} ({_in_days(days)})" for name, days in bdays)


def _period(view: DigestView) -> str:
    return f"{view.week_start.strftime('%d.%m')}–{view.week_end.strftime('%d.%m')}"


# --- факты для AI-промпта (только цифры/имена, персона обёртывает их сама) ---


def facts_block(view: DigestView) -> str:
    lines = [f"Итоги недели {_period(view)}."]
    lines.append(f"- Сообщений за неделю: {view.messages}{_delta(view.messages_delta)}.")
    lines.append(f"- Времени в голосовых: {view.voice_hours} ч{_delta(view.voice_delta, ' ч')}.")
    if view.members_delta:
        lines.append(f"- Участников за неделю: {view.members_delta:+d}.")
    if view.peak_day_name:
        lines.append(
            f"- Самый живой день: {view.peak_day_name} ({view.peak_day_messages} сообщений)."
        )
    if view.stars:
        lines.append(f"- На вершине по очкам: {_people(view.stars)}.")
    if view.birthdays:
        lines.append(f"- Скоро дни рождения: {_birthdays(view.birthdays)}.")
    if view.watched_titles:
        lines.append(f"- Смотрели в киноклубе: {', '.join(view.watched_titles)}.")
    if view.top_collector:
        lines.append(
            f"- Коллекционер: {view.top_collector.name} ({view.top_collector.detail})."
        )
    return "\n".join(lines)


# --- шаблонный фолбэк без ИИ (детерминированный пост) ---


def render_plain(view: DigestView) -> str:
    lines = [f"🌙 **Итоги недели** ({_period(view)})", ""]
    lines.append(
        f"За неделю: {view.messages} сообщений{_delta(view.messages_delta)}, "
        f"{view.voice_hours} ч в голосовых{_delta(view.voice_delta, ' ч')}."
    )
    if view.peak_day_name:
        lines.append(
            f"🔥 Оживлённее всего было в {view.peak_day_name} — {view.peak_day_messages} сообщений."
        )
    if view.members_delta > 0:
        lines.append(f"🌱 Нас стало на {view.members_delta} больше.")
    elif view.members_delta < 0:
        lines.append(f"🍂 Стало на {abs(view.members_delta)} меньше — но мы на месте.")
    if view.stars:
        lines.append(f"✨ На вершине: {_people(view.stars)}.")
    if view.birthdays:
        lines.append(f"🎂 Дни рождения на подходе: {_birthdays(view.birthdays)}.")
    if view.watched_titles:
        lines.append(f"🎬 В киноклубе смотрели: {', '.join(view.watched_titles)}.")
    if view.top_collector:
        lines.append(
            f"🎁 Коллекционер недели — {view.top_collector.name} "
            f"({view.top_collector.detail})."
        )
    return "\n".join(lines)
