from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class SecretCode:
    guild_id: int
    user_id: int
    code: str
    issued_at: datetime
    used_at: datetime | None = None


@dataclass(frozen=True)
class SecretRoom:
    guild_id: int
    text_channel_id: int
    voice_channel_id: int
    expires_at: datetime
    created_by: int
    id: int | None = None


@dataclass
class RelationshipProfile:
    """Агрегат отношений персоны с пользователем в рамках гильдии.
    Уровень и роль здесь не хранятся — они детерминированно вычисляются
    из очков политикой PointsToLevelPolicy."""

    user_id: int
    guild_id: int
    points: int = 0
    points_awarded_today: int = 0
    last_award_date: date | None = None
    is_exclusive: bool = False
    frozen_by_admin: bool = False
    last_dialog_at: datetime | None = None
    user_notes: str = ""
    # анкета знакомства (то, что человек сам о себе сказал; заметки — то,
    # что персона узнала сама: в промпт подаются раздельно)
    survey_gender: str = ""       # "парень" | "девушка" | "инкогнито"
    survey_contact: str = ""      # "quiet" | "normal" | "attention" ("" = normal)
    survey_interests: str = ""    # через запятую
    survey_season: str = ""       # "весна" | "лето" | "осень" | "зима"
    survey_completed_at: datetime | None = None
    # день рождения (без года) и дедупликация уведомлений по годам
    birthday_day: int | None = None
    birthday_month: int | None = None
    birthday_reminded_at: date | None = None
    birthday_congratulated_at: date | None = None
    deep_dialogs: int = 0          # «глубина»: сколько было долгих разговоров
    last_decay_at: date | None = None  # дедупликация угасания очков

    def award_point(self, now: datetime, daily_cap: int, amount: int = 1) -> bool:
        """Очки за адресованное персоне сообщение, с дневным потолком
        (amount > 1 в праздники). Возвращает True, если начислено.
        last_dialog_at обновляется в любом случае — от него зависит
        «возвращение после перерыва», а не от начисления."""
        self.last_dialog_at = now
        if self.frozen_by_admin:
            return False
        today = now.date()
        if self.last_award_date != today:
            self.last_award_date = today
            self.points_awarded_today = 0
        if self.points_awarded_today >= daily_cap:
            return False
        self.points += amount
        self.points_awarded_today += amount
        return True
