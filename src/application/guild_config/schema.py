"""Единый источник правды по пер-серверным настройкам.

`GuildSettings` — pydantic-модель со ВСЕМИ ключами, которые сервер может
переопределять через /config: их типы, диапазоны и дефолты. Что не описано
здесь — переопределить нельзя в принципе (граница «поведение фич — per-guild,
секреты и инфраструктура — только .env»).

Дефолты дублируют `src.config.Settings` намеренно: глобальные значения из .env
работают как база для всех гильдий, а модель задаёт валидацию и станет схемой
формы для /config и будущей веб-панели. Чтобы два источника дефолтов не
разъехались, есть тест-сверка поле-в-поле (tests/test_guild_config_schema.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.application.interfaces.entitlements import PlanTier
from src.domain.relationship.policies import PointsToLevelPolicy

# ключи-каналы (ID; 0 = выключено) — /config покажет их пикером канала.
# Пикер отдаёт discord.abc.GuildChannel, поэтому сюда годится и категория
# (tempvoice_category), а не только текстовый/голосовой канал.
CHANNEL_KEYS: frozenset[str] = frozenset(
    {
        "cinema_forum_channel",
        "git_forum_channel",
        "steam_forum_channel",
        "digest_channel",
        "appeals_channel",
        "finds_channel_id",
        "tempvoice_hub_channel",
        "tempvoice_category",
        "main_channel_id",
        "welcome_channel_id",
        "album_channel_id",
    }
)


class GuildSettings(BaseModel):
    """Разрешённые для /config настройки одного сервера. frozen: значение
    настроек нельзя мутировать на месте — только пересобрать."""

    model_config = ConfigDict(frozen=True)

    # --- модерация ---
    warn_threshold: int = Field(3, ge=1, le=20)
    # кросс-серверные баны: порог «отмеченного» участника (забанен на N серверах)
    banwatch_threshold: int = Field(3, ge=1, le=50)
    warn_mute_minutes: int = Field(120, ge=1, le=40320)
    # затухание варнов: старше N дней не считаются к порогу (0 = не затухают)
    warn_expire_days: int = Field(90, ge=0, le=3650)
    # лестница эскалации: 1-е достижение порога — мут, 2-е — мут ×3, 3-е+ — tempban
    warn_escalation: bool = True
    warn_ban_minutes: int = Field(1440, ge=1, le=525600)
    # слать наказанному ЛС с причиной/сроком (варн/мут/кик/бан)
    moderation_dm_notice: bool = True
    spam_limit: int = Field(5, ge=2, le=50)
    spam_window: int = Field(10, ge=3, le=120)
    spam_mute_minutes: int = Field(2, ge=1, le=1440)
    # масс-упоминания: > N упоминаний в одном сообщении -> мут (0 = выкл)
    spam_mention_limit: int = Field(5, ge=0, le=50)
    # удалять чужие Discord-инвайты от не-модеров (и варнить)
    spam_block_invites: bool = False

    # --- отношения и роли ---
    relationship_daily_point_cap: int = Field(20, ge=1, le=5000)
    relationship_decay_after_days: int = Field(30, ge=1, le=365)
    relationship_decay_every_days: int = Field(3, ge=1, le=90)
    relationship_decay_points: int = Field(1, ge=0, le=100)
    relationship_role_thresholds: list[int] = Field(
        default=[100, 250, 450, 700, 950, 1200], min_length=1, max_length=20
    )
    relationship_exclusive_threshold: int = Field(1250, ge=1)
    relationship_absence_days: int = Field(30, ge=1, le=365)
    relationship_notes_max_chars: int = Field(700, ge=100, le=4000)
    relationship_role_names: list[str] = Field(
        default=[
            "☕ Случайный прохожий",
            "🌧 Знакомый силуэт",
            "🎨 Занятный собеседник",
            "🎧 На одной волне",
            "🍷 Вечерняя компания",
            "🖤 Особенный",
            "✂️👁🖤 Единственный",
        ],
        min_length=2,
        max_length=21,
    )
    # автовыдача ролей при входе (id ролей; пусто = выкл). Список — редактируется
    # на вкладке «Роли», не в /config. Валидность ролей (ниже бота, не managed)
    # проверяет API при записи и бот при выдаче — модель их знать не может.
    autorole_ids: list[int] = Field(default=[], max_length=20)
    # роль-«ступень 0» (0–99 очков): имя роли, которую держат все до первой
    # статус-роли. Пусто = выключено. Отдельно от relationship_role_names (тот
    # жёстко = число порогов + 1); эта роль соответствует role_index None.
    relationship_newcomer_role: str = Field(default="", max_length=100)
    secret_room_min_level: int = Field(5, ge=1, le=7)
    secret_room_hours: int = Field(12, ge=1, le=168)
    survey_bonus_points: int = Field(5, ge=0, le=500)
    # интерес из анкеты /introduce -> Discord-роль (пусто = только профиль).
    # Ключ — название интереса, значение — id роли. Валидность роли (ниже бота,
    # не managed) проверяет бот при выдаче — модель её знать не может.
    interest_roles: dict[str, int] = Field(default={})
    birthday_remind_days: int = Field(3, ge=0, le=30)
    holiday_points_multiplier: int = Field(2, ge=1, le=10)

    # --- поведение AI (инфра AI: retry/circuit breaker/таймауты — в .env) ---
    ai_context_messages: int = Field(25, ge=1, le=100)
    ai_notes_update_every: int = Field(10, ge=1, le=100)
    ai_dialog_gap_minutes: int = Field(30, ge=1, le=1440)
    ai_dialog_min_exchanges: int = Field(3, ge=1, le=50)
    ai_deep_dialog_exchanges: int = Field(5, ge=1, le=100)
    ai_dialog_summary_keep: int = Field(5, ge=1, le=50)
    ai_event_comment_chance: float = Field(0.12, ge=0.0, le=1.0)
    ai_event_comment_cooldown: int = Field(900, ge=0, le=86400)
    ai_rate_limits_by_level: dict[int, int] = Field(
        default={1: 5, 2: 10, 3: 20, 4: 40, 5: 60, 6: 120, 7: 240}
    )
    # пассивное вклинивание в разговоры (включено; консервативно по умолчанию)
    ai_passive_enabled: bool = True
    ai_passive_only_main_channel: bool = True
    ai_passive_min_users: int = Field(2, ge=2, le=10)
    ai_passive_cooldown_minutes: int = Field(12, ge=1, le=180)
    ai_passive_confidence_min: float = Field(0.7, ge=0.0, le=1.0)

    # --- активность ---
    voice_points_per_hour: int = Field(3, ge=0, le=100)
    lonely_hours: int = Field(12, ge=1, le=168)
    absent_days_threshold: int = Field(7, ge=1, le=365)

    # --- тумблеры модуля «Активность» (per-server вкл/выкл, вкладка «Модули»);
    # мастер выкл => все подфункции выкл (наследование в хелпере) ---
    activity_enabled: bool = True
    activity_greetings: bool = True
    activity_return_remarks: bool = True
    activity_album: bool = True
    activity_voice_points: bool = True
    activity_holidays: bool = True
    activity_birthdays: bool = True
    activity_decay: bool = True
    activity_lonely: bool = True
    activity_random_thoughts: bool = True

    # --- киноклуб ---
    cinema_rating_hours: int = Field(24, ge=1, le=336)
    cinema_watchlist_max: int = Field(50, ge=5, le=500)
    cinema_forum_channel: int = Field(0, ge=0)

    # --- GitHub-репозитории (/git) ---
    git_forum_channel: int = Field(0, ge=0)  # форум-канал для тредов релизов (0=выкл)
    # роль-менеджер /git (ID или имя роли; пусто = только менеджеры сервера)
    git_manager_role: str = Field(default="", max_length=100)

    # --- Steam-игры (/steam) ---
    steam_forum_channel: int = Field(0, ge=0)  # форум-канал для тредов игр (0=выкл)
    steam_manager_role: str = Field(default="", max_length=100)  # роль-менеджер /steam
    digest_channel: int = Field(0, ge=0)  # канал недельного дайджеста (0 = не постить)
    appeals_channel: int = Field(0, ge=0)  # канал очереди апелляций (0 = выключено)

    # --- находки ---
    finds_channel_id: int = Field(0, ge=0)
    # каналы активности по ID (0 = легаси-имя из .env → фолбэк на system_channel)
    main_channel_id: int = Field(0, ge=0)
    welcome_channel_id: int = Field(0, ge=0)
    album_channel_id: int = Field(0, ge=0)
    finds_min_interval_hours: int = Field(12, ge=1, le=336)
    finds_max_interval_hours: int = Field(48, ge=1, le=720)
    finds_fail_penalty: int = Field(5, ge=0, le=500)
    finds_claim_cooldown_hours: int = Field(8, ge=0, le=168)

    # --- «остаться или уйти» ---
    staykick_enabled: bool = False
    staykick_hours: int = Field(12, ge=1, le=168)

    # --- временные голосовые каналы («каморки») ---
    tempvoice_hub_channel: int = Field(0, ge=0)  # 0 = фича выключена
    tempvoice_category: int = Field(0, ge=0)
    # 50 — жёсткий лимит Discord на число каналов в одной категории
    tempvoice_max_per_guild: int = Field(25, ge=1, le=50)
    tempvoice_default_limit: int = Field(0, ge=0, le=99)  # 99 — максимум Discord
    # тумблеры модуля «Каморки» (вкладка «Модули»)
    tempvoice_enabled: bool = True
    tempvoice_panel: bool = True

    # тумблеры прочих модулей (мастера, вкладка «Модули»); staykick_enabled ниже
    fun_enabled: bool = True
    introduce_enabled: bool = True
    secret_room_enabled: bool = True
    music_enabled: bool = True
    cinema_enabled: bool = True
    finds_enabled: bool = True
    git_enabled: bool = True
    steam_enabled: bool = True
    banwatch_enabled: bool = True
    digest_enabled: bool = True
    appeals_enabled: bool = True
    achievements_enabled: bool = True

    # --- тумблеры модуля «Модерация» (мастер гасит все админ-команды;
    # авторазбан работает всегда, чтобы tempban'ы снимались) ---
    moderation_enabled: bool = True
    moderation_antispam: bool = True
    # --- тумблеры модуля «AI-чат» (ai_passive_enabled выше — подфлаг «пассив») ---
    ai_chat_enabled: bool = True
    ai_reactive: bool = True
    ai_event_comments: bool = True
    # --- тумблеры модуля «Отношения и роли» (очки копятся в activity/ai; здесь —
    # команды и физическая выдача Discord-ролей) ---
    relationship_enabled: bool = True
    relationship_role_sync: bool = True

    # --- музыка ---
    music_karaoke_ansi: bool = False

    # --- утилиты (модуль «Развлечения») ---
    # лимит /send на человека в час. Раньше был только глобальным (config.py);
    # перенесён в пер-серверную схему, чтобы стал настраиваемым и тарифицируемым
    # (кламп идёт через общий шов, т.к. fun-ког читает его через провайдер).
    send_per_hour: int = Field(5, ge=1, le=1000)

    # --- кросс-полевые инварианты ---

    @model_validator(mode="after")
    def _check_thresholds_and_roles(self) -> GuildSettings:
        thresholds = self.relationship_role_thresholds
        if any(b <= a for a, b in zip(thresholds, thresholds[1:], strict=False)):
            raise ValueError("пороги ролей должны строго возрастать")
        if self.relationship_exclusive_threshold <= thresholds[-1]:
            raise ValueError("эксклюзивный порог должен быть больше последнего порога ролей")
        # имён ролей = число порогов + 1 (последнее имя — «эксклюзив»)
        if len(self.relationship_role_names) != len(thresholds) + 1:
            raise ValueError(
                f"имён ролей должно быть {len(thresholds) + 1} "
                f"(порогов {len(thresholds)} + эксклюзив), а не {len(self.relationship_role_names)}"
            )
        return self

    @model_validator(mode="after")
    def _check_finds_interval(self) -> GuildSettings:
        if self.finds_max_interval_hours < self.finds_min_interval_hours:
            raise ValueError("макс. интервал находок не может быть меньше минимального")
        return self

    # --- удобные фабрики доменных объектов из настроек сервера ---

    def points_policy(self) -> PointsToLevelPolicy:
        return PointsToLevelPolicy(
            thresholds=tuple(self.relationship_role_thresholds),
            exclusive_threshold=self.relationship_exclusive_threshold,
        )


# все редактируемые ключи — для автокомплита /config и тест-сверки
SETTING_KEYS: tuple[str, ...] = tuple(GuildSettings.model_fields.keys())


def _kind(key: str) -> Literal["bool", "channel", "float", "list", "dict", "text", "int"]:
    """Категория ключа для UX /config (какой пикер показать)."""
    field = GuildSettings.model_fields[key]
    ann = field.annotation
    if ann is bool:
        return "bool"
    if key in CHANNEL_KEYS:
        return "channel"
    if ann is float:
        return "float"
    if ann is str:
        return "text"
    origin = getattr(ann, "__origin__", None)
    if origin is list:
        return "list"
    if origin is dict:
        return "dict"
    return "int"


# ключ -> категория значения (bool/channel/float/list/dict/int)
KEY_KINDS: dict[str, str] = {k: _kind(k) for k in SETTING_KEYS}


# --- реестр тарифицируемых лимитов (подготовка к монетизации) ---------------
# ВНИМАНИЕ: это ТОЛЬКО данные. Enforcement (кламп по тарифу) здесь не живёт и
# нигде пока не подключён — см. docs/plans/monetization-prep.md, Prep 1/2.
# Значения free-потолков предварительные (не привязаны к коду, легко менять).


class ClampDir(Enum):
    """Как free-тариф зажимает настроенное админом значение."""

    MAX = "max"  # эффективное = min(configured, free_limit)  — «не выше потолка»
    MIN = "min"  # эффективное = max(configured, free_limit)  — «не ниже пола»


@dataclass(frozen=True)
class TierCap:
    """Описание одного тарифицируемого лимита.

    free_limit — потолок (MAX) или пол (MIN) для free-тарифа.
    special — непустое => лимит нескалярный и требует кастомного кламп-кода
    (dict/list): простой min/max к нему неприменим."""

    free_limit: int
    direction: ClampDir
    special: str = ""  # "" | "dict_per_level" | "list_length"
    note: str = ""


# ключ настройки -> как его зажимает free-тариф. Premium/Pro = без клампа.
TIERABLE: dict[str, TierCap] = {
    # скалярные потолки (free = не выше)
    "tempvoice_max_per_guild": TierCap(5, ClampDir.MAX, note="каморки: free 3–5"),
    "cinema_watchlist_max": TierCap(15, ClampDir.MAX, note="вотчлист короче"),
    "relationship_notes_max_chars": TierCap(300, ClampDir.MAX, note="заметки Попоси урезаны"),
    "ai_context_messages": TierCap(10, ClampDir.MAX, note="глубина памяти AI"),
    "ai_dialog_summary_keep": TierCap(1, ClampDir.MAX, note="AI-память: free 0–1 резюме"),
    "send_per_hour": TierCap(2, ClampDir.MAX, note="/send: free ~2/час"),
    # скалярные полы (free = не ниже => реже/дольше)
    "finds_min_interval_hours": TierCap(24, ClampDir.MIN, note="находки реже"),
    "finds_claim_cooldown_hours": TierCap(24, ClampDir.MIN, note="длиннее кулдаун похода"),
    # нескалярные — кламп кастомным кодом (не простым min/max)
    "ai_rate_limits_by_level": TierCap(
        0, ClampDir.MAX, special="dict_per_level", note="ГЛАВНЫЙ AI-paywall; кламп по уровням"
    ),
    "autorole_ids": TierCap(1, ClampDir.MAX, special="list_length", note="free: 1 автороль"),
}

# ключи, которые тарифом НЕ трогаются никогда (для тест-инварианта против
# случайного дубля ключа и в TIERABLE, и в списке «неприкосновенных»).
TIER_NEVER: frozenset[str] = frozenset(
    {
        "relationship_daily_point_cap",  # баланс, не paywall
        "warn_threshold",
        "spam_limit",
    }
)

# мастер-тумблер модуля -> минимальный тариф, на котором модуль доступен.
# Отсутствие ключа = free (доступен всем). Только ДАННЫЕ; enforcement — будущий
# require_tier (сегодня no-op, т.к. заглушка тарифов выдаёт PRO). Подфункция
# activity_album здесь — Premium-«вау» внутри free-модуля «Активность».
MODULE_MIN_TIER: dict[str, PlanTier] = {
    "git_enabled": PlanTier.PREMIUM,
    "steam_enabled": PlanTier.PREMIUM,
    "digest_enabled": PlanTier.PREMIUM,
    "achievements_enabled": PlanTier.PREMIUM,
    "secret_room_enabled": PlanTier.PREMIUM,
    "activity_album": PlanTier.PREMIUM,
    "staykick_enabled": PlanTier.PRO,
}
