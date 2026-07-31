from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Warn:
    guild_id: int
    user_id: int
    moderator_id: int
    reason: str
    created_at: datetime
    id: int | None = None


@dataclass(frozen=True)
class TempBan:
    guild_id: int
    user_id: int
    moderator_id: int
    reason: str
    expires_at: datetime
    id: int | None = None


# действия для единого журнала кейсов (человекочитаемая история наказаний).
# Значения совпадают с тем, что кладём в mod_cases.action — не меняй ретроактивно.
CASE_WARN = "warn"
CASE_WARN_MUTE = "warn_mute"  # авто-мут по достижении порога варнов
CASE_WARN_TEMPBAN = "warn_tempban"  # авто-tempban для рецидивиста (эскалация)
CASE_MUTE = "mute"
CASE_UNMUTE = "unmute"
CASE_KICK = "kick"
CASE_BAN = "ban"  # постоянный бан
CASE_TEMPBAN = "tempban"
CASE_UNBAN = "unban"
CASE_CLEARWARNS = "clearwarns"
CASE_CLEAR = "clear"
CASE_SPAM_MUTE = "spam_mute"
CASE_RAGE = "rage"

# наказания, по которым считаем «рецидив» для лестницы эскалации варнов
ESCALATION_CASES = (CASE_WARN_MUTE, CASE_WARN_TEMPBAN)


@dataclass(frozen=True)
class ModCase:
    """Одна запись единого журнала действий модерации (бот И панель пишут сюда).
    Заменяет разрозненный лог-канал как источник истории по участнику."""

    guild_id: int
    user_id: int  # цель действия
    moderator_id: int  # кто применил (0 = автоматика: антиспам/эскалация)
    action: str
    reason: str
    created_at: datetime
    duration_minutes: int | None = None  # для мута/tempban
    source: str = "bot"  # bot | panel
    id: int | None = None
