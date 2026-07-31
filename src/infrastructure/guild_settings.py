"""Пер-гильдийные настройки: сервис поверх единой модели GuildSettings.

Что редактируемо — задаёт схема `GuildSettings` (application-слой): реестр
SETTING_SPECS генерируется из её полей, диапазоны берутся из ограничений
полей. Секреты (токен, ключи API, БД) в модель не входят по определению.

Переопределения сервера лежат в таблице guild_settings и целиком грузятся в
память при старте (load_all). Чтение (get/current/resolved) — синхронное, из
кэша, без похода в БД: годится для горячих путей. Запись (set/reset) идёт в БД,
обновляет кэш и сбрасывает мемоизированную resolved-модель.

Валидация двухуровневая: SettingSpec.parse проверяет тип и диапазон одного
поля (понятные сообщения для /config), а перед сохранением собирается ПОЛНАЯ
GuildSettings — она ловит кросс-полевые инварианты (пороги ↔ имена ролей,
интервал находок min ≤ max, эксклюзивный порог)."""

import json
import logging
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.guild_config.schema import (
    KEY_KINDS,
    SETTING_KEYS,
    GuildSettings,
)
from src.application.interfaces.settings_provider import ISettingsProvider
from src.config import Settings
from src.infrastructure.db.models.guild import GuildSettingModel

logger = logging.getLogger(__name__)

# скаляры хранятся как str(value); списки/словари — как JSON
_SCALAR_KINDS = frozenset({"int", "float", "bool", "channel", "text"})
_COMPLEX_KINDS = frozenset({"list", "dict"})

# Канал Postgres LISTEN/NOTIFY: любой процесс (веб-панель), записав настройку,
# шлёт сюда guild_id, а бот перечитывает кэш этой гильдии (SettingsChangeListener).
# Только Postgres; на SQLite панель как второй писатель невозможна.
SETTINGS_NOTIFY_CHANNEL = "poposya_settings"

# человекочитаемые подписи и единицы для /config; ключей без записи здесь нет,
# но на всякий случай fallback — сам ключ
_LABELS: dict[str, tuple[str, str]] = {
    # модерация
    "warn_threshold": ("Варнов до мута", ""),
    "warn_mute_minutes": ("Длительность мута по варнам", "мин"),
    "spam_limit": ("Сообщений за окно = спам", ""),
    "spam_window": ("Окно антиспама", "сек"),
    "spam_mute_minutes": ("Мут за спам", "мин"),
    # отношения и роли
    "relationship_daily_point_cap": ("Дневной потолок очков", ""),
    "relationship_decay_after_days": ("Дней тишины до угасания очков", "дн"),
    "relationship_decay_every_days": ("Как часто списывать при угасании", "дн"),
    "relationship_decay_points": ("Сколько очков списывать за раз", "очк"),
    "relationship_exclusive_threshold": ("Порог «Единственного»", "очк"),
    "relationship_absence_days": ("Дней отсутствия — сброс серии", "дн"),
    "relationship_notes_max_chars": ("Лимит заметки о человеке", "симв"),
    "relationship_newcomer_role": ("Роль новичка (до 1 уровня; пусто = выкл)", ""),
    "secret_room_min_level": ("Уровень для тайной комнаты", ""),
    "secret_room_hours": ("Время жизни тайной комнаты", "ч"),
    "survey_bonus_points": ("Бонус за анкету", "очк"),
    "birthday_remind_days": ("За сколько напоминать о ДР", "дн"),
    "holiday_points_multiplier": ("Множитель очков в праздники", "x"),
    # поведение AI
    "ai_context_messages": ("Сообщений канала в контекст", ""),
    "ai_notes_update_every": ("Обновлять заметку каждые N очков", "очк"),
    "ai_dialog_gap_minutes": ("Пауза = конец диалога", "мин"),
    "ai_dialog_min_exchanges": ("Мин. обменов для резюме", ""),
    "ai_deep_dialog_exchanges": ("Обменов = «долгий диалог»", ""),
    "ai_dialog_summary_keep": ("Сколько резюме хранить", ""),
    "ai_event_comment_chance": ("Шанс реплики на событие (0..1)", ""),
    "ai_event_comment_cooldown": ("Кулдаун реплик на события", "сек"),
    "ai_passive_enabled": ("Пассивное вклинивание в разговоры", ""),
    "ai_passive_only_main_channel": ("Пассив только в главном канале", ""),
    "ai_passive_min_users": ("Мин. людей в разговоре для пассива", ""),
    "ai_passive_cooldown_minutes": ("Кулдаун пассивных реплик", "мин"),
    "ai_passive_confidence_min": ("Порог уверенности «встрять» (0..1)", ""),
    # активность
    "voice_points_per_hour": ("Очки за час в войсе (0=выкл)", ""),
    "lonely_hours": ("Часов тишины до «скучаю»", "ч"),
    "absent_days_threshold": ("Дней отсутствия до «с возвращением»", "дн"),
    # тумблеры модуля «Активность» (вкладка «Модули»)
    "activity_enabled": ("Активность (весь модуль)", ""),
    "activity_greetings": ("Приветствия и прощания", ""),
    "activity_return_remarks": ("Реплики о возвращении", ""),
    "activity_album": ("Альбом Попоси", ""),
    "activity_voice_points": ("Очки за войс", ""),
    "activity_holidays": ("Праздники", ""),
    "activity_birthdays": ("Дни рождения", ""),
    "activity_decay": ("Угасание очков", ""),
    "activity_lonely": ("«Скучаю» в тишине", ""),
    "activity_random_thoughts": ("Случайные мысли", ""),
    # киноклуб
    "cinema_rating_hours": ("Сбор оценок после просмотра", "ч"),
    "cinema_watchlist_max": ("Предел вотчлиста", ""),
    "cinema_forum_channel": ("Форум «золотой фонд» (0=выкл)", ""),
    # GitHub-репозитории
    "git_forum_channel": ("Форум релизов GitHub (0=выкл)", ""),
    # Steam-игры
    "steam_forum_channel": ("Форум новостей Steam (0=выкл)", ""),
    # находки
    "finds_channel_id": ("Канал находок (0 = по имени / MAIN)", ""),
    "finds_min_interval_hours": ("Мин. интервал находок", "ч"),
    "finds_max_interval_hours": ("Макс. интервал находок", "ч"),
    "finds_fail_penalty": ("Штраф за провал похода", "очк"),
    "finds_claim_cooldown_hours": ("Кулдаун похода", "ч"),
    # музыка
    "music_karaoke_ansi": ("Цветное караоке (ANSI)", ""),
    # «остаться или уйти»
    "staykick_enabled": ("Остаться или уйти (весь модуль)", ""),
    "staykick_hours": ("Через сколько часов авто-кик", "ч"),
    # каморки (временные голосовые каналы)
    "tempvoice_hub_channel": ("Канал-хаб «создать каморку» (0=выкл)", ""),
    "tempvoice_category": ("Категория каморок (0 = категория хаба)", ""),
    "tempvoice_max_per_guild": ("Потолок каморок на сервере", ""),
    "tempvoice_default_limit": ("Мест в новой каморке (0=без лимита)", ""),
    # тумблеры модуля «Каморки» (вкладка «Модули»)
    "tempvoice_enabled": ("Каморки (весь модуль)", ""),
    "tempvoice_panel": ("Панель управления каналом", ""),
    # мастера прочих модулей
    "fun_enabled": ("Развлечения (весь модуль)", ""),
    "introduce_enabled": ("Знакомство/анкета (весь модуль)", ""),
    "secret_room_enabled": ("Тайная комната (весь модуль)", ""),
    "music_enabled": ("Музыка (весь модуль)", ""),
    "cinema_enabled": ("Киноклуб (весь модуль)", ""),
    "finds_enabled": ("Находки (весь модуль)", ""),
    "git_enabled": ("GitHub-репозитории (весь модуль)", ""),
    "steam_enabled": ("Steam-игры (весь модуль)", ""),
    # тумблеры модуля «Модерация»
    "moderation_enabled": ("Модерация (весь модуль)", ""),
    "moderation_antispam": ("Антиспам (авто-мут за флуд)", ""),
    # тумблеры модуля «AI-чат» (ai_passive_enabled — подфлаг «пассив», см. выше)
    "ai_chat_enabled": ("AI-чат (весь модуль)", ""),
    "ai_reactive": ("Ответы на обращения", ""),
    "ai_event_comments": ("Комментарии к включённым трекам", ""),
    # тумблеры модуля «Отношения и роли»
    "relationship_enabled": ("Отношения и роли (весь модуль)", ""),
    "relationship_role_sync": ("Выдача Discord-ролей", ""),
}


def _field_range(key: str) -> tuple[int | float | None, int | float | None]:
    """min/max ключа из ограничений поля модели (ge/gt, le/lt)."""
    lo: int | float | None = None
    hi: int | float | None = None
    for meta in GuildSettings.model_fields[key].metadata:
        for attr in ("ge", "gt"):
            if hasattr(meta, attr):
                lo = getattr(meta, attr)
        for attr in ("le", "lt"):
            if hasattr(meta, attr):
                hi = getattr(meta, attr)
    return lo, hi


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    kind: str  # "int" | "float" | "channel" | "bool" | "text"
    min: int | float | None = None
    max: int | float | None = None
    unit: str = ""  # для подсказки: «ч», «мин», «сек», «очк»…

    def parse(self, raw: str) -> int | float | str:
        """Текст из команды -> валидное значение или ValueError с понятным текстом."""
        raw = raw.strip()
        if self.kind == "text":
            # длину/содержимое проверит pydantic при сборке модели (max_length)
            return raw
        if self.kind == "bool":
            low = raw.lower()
            if low in ("1", "true", "on", "вкл", "да", "yes"):
                return 1
            if low in ("0", "false", "off", "выкл", "нет", "no"):
                return 0
            raise ValueError("нужно вкл/выкл (1/0)")
        if self.kind == "channel":
            digits = raw.strip("<#>").strip()
            if not digits.isdigit():
                raise ValueError("нужен ID канала или упоминание (#канал), либо 0 — выключить")
            return int(digits)
        if self.kind == "float":
            try:
                fvalue = float(raw.replace(",", "."))
            except ValueError:
                raise ValueError("нужно число") from None
            return self._check_range(fvalue)
        try:
            value = int(raw)
        except ValueError:
            raise ValueError("нужно целое число") from None
        return int(self._check_range(value))

    def _check_range(self, value: int | float) -> int | float:
        if self.min is not None and value < self.min:
            raise ValueError(f"минимум {self.min}")
        if self.max is not None and value > self.max:
            raise ValueError(f"максимум {self.max}")
        return value


@dataclass(frozen=True)
class ModuleSpec:
    """Модуль на вкладке «Модули»: мастер-флаг + подфункции. Все ключи —
    обычные bool-настройки GuildSettings; наследование (мастер выкл ⇒ подфункции
    выкл) применяет хелпер в боте, а не хранилище."""

    key: str  # "activity"
    label: str  # "Активность"
    master: str  # ключ мастер-флага
    subs: tuple[str, ...]  # ключи подфункций
    description: str = ""  # что входит в модуль (подпись на карточке)


# Реестр отключаемых модулей. Новый модуль = добавить сюда + гарды в его коге.
FEATURE_MODULES: tuple[ModuleSpec, ...] = (
    ModuleSpec(
        key="activity",
        label="Активность",
        master="activity_enabled",
        subs=(
            "activity_greetings",
            "activity_return_remarks",
            "activity_album",
            "activity_voice_points",
            "activity_holidays",
            "activity_birthdays",
            "activity_decay",
            "activity_lonely",
            "activity_random_thoughts",
        ),
        description=(
            "Живость бота: приветствия и прощания, реакция на возвращение, «Альбом» "
            "лучших сообщений, очки за время в войсе, праздники и дни рождения, "
            "угасание очков за молчание, реплики «скучаю» и случайные мысли."
        ),
    ),
    ModuleSpec(
        key="tempvoice",
        label="Каморки (временные войс-каналы)",
        master="tempvoice_enabled",
        subs=("tempvoice_panel",),
        description=(
            "Свой временный голосовой канал по входу в хаб-канал; удаляется, когда "
            "пустеет. Подфункция — панель управления каналом (переименование, лимит "
            "мест, замок, скрытие) для владельца."
        ),
    ),
    ModuleSpec(
        key="fun",
        label="Развлечения",
        master="fun_enabled",
        subs=(),
        description=(
            "Команды: /dice (кубик/дуэль), /coinflip, /topic, /profile, /serverstats, "
            "/rules, /send (сообщение через бота в ЛС), /remind (напоминание), "
            "/birthday (указать день рождения)."
        ),
    ),
    ModuleSpec(
        key="introduce",
        label="Знакомство и анкета",
        master="introduce_enabled",
        subs=(),
        description=(
            "Анкета участника (интересы, «о себе», предпочтения по общению) и бонус "
            "очков за заполнение."
        ),
    ),
    ModuleSpec(
        key="secret_room",
        label="Тайная комната",
        master="secret_room_enabled",
        subs=(),
        description=(
            "По достижении высокого уровня бот в ЛС выдаёт ключ; команда /secret "
            "открывает скрытые текст+войс каналы «для своих» на несколько часов."
        ),
    ),
    ModuleSpec(
        key="staykick",
        label="Остаться или уйти",
        master="staykick_enabled",
        subs=(),
        description=(
            "Новичку в ЛС приходит выбор «остаться/уйти»; кто не остался за отведённое "
            "время — автоматически кикается."
        ),
    ),
    ModuleSpec(
        key="music",
        label="Музыка",
        master="music_enabled",
        subs=(),
        description=(
            "Проигрывание в голосовых: /play, очередь, пропуск/перемотка, плейлисты, "
            "лайки, радио (авто-подбор похожего) и караоке (текст песни)."
        ),
    ),
    ModuleSpec(
        key="cinema",
        label="Киноклуб",
        master="cinema_enabled",
        subs=(),
        description=(
            "Вотчлист фильмов с голосованием, киновечера с опросом, оценки после "
            "просмотра и «золотой фонд»."
        ),
    ),
    ModuleSpec(
        key="finds",
        label="Находки",
        master="finds_enabled",
        subs=(),
        description=(
            "Ночные находки («Токийские трофеи»): бот подкидывает предметы, участники "
            "ловят их и ходят в «прогулки», собирают коллекции и дарят Попосе."
        ),
    ),
    ModuleSpec(
        key="git",
        label="GitHub-репозитории",
        master="git_enabled",
        subs=(),
        description=(
            "Команда /git подписывает репозитории GitHub: бот заводит по каждому тред "
            "в форум-канале и постит туда новые релизы. Форум задаётся настройкой "
            "git_forum_channel."
        ),
    ),
    ModuleSpec(
        key="steam",
        label="Steam-игры",
        master="steam_enabled",
        subs=(),
        description=(
            "Команда /steam подписывает игры Steam: бот заводит по каждой тред в "
            "форум-канале и постит туда официальные новости — обновления, патчи, "
            "анонсы — с картинкой. Форум задаётся настройкой steam_forum_channel."
        ),
    ),
    ModuleSpec(
        key="moderation",
        label="Модерация",
        master="moderation_enabled",
        subs=("moderation_antispam",),
        description=(
            "Админ-команды: варны с авто-мутом, мут/анмут, временные баны с "
            "авторазбаном, /clear, /slowmode, /say, /rage. Подфункция — автоматический "
            "антиспам (предупреждение и мут за флуд). Авторазбан истёкших банов "
            "работает всегда, даже при выключенном модуле."
        ),
    ),
    ModuleSpec(
        key="ai_chat",
        label="AI-чат",
        master="ai_chat_enabled",
        subs=("ai_reactive", "ai_passive_enabled", "ai_event_comments"),
        description=(
            "Живое общение Попоси через ИИ. Подфункции: ответы на упоминания и реплаи, "
            "пассивное вклинивание в разговоры (сама решает встрять) и комментарии к "
            "включённым в голосовом трекам. Тонкая настройка пассива — во вкладке «Настройки»."
        ),
    ),
    ModuleSpec(
        key="relationship",
        label="Отношения и роли",
        master="relationship_enabled",
        subs=("relationship_role_sync",),
        description=(
            "Команды /rank и /leaderboard, админ-управление очками и физическая выдача "
            "Discord-ролей за статус. Подфункция — только выдача ролей (очки продолжают "
            "копиться от общения и войса даже при выключенной выдаче)."
        ),
    ),
)

# все ключи-флаги — их прячем из /config и вкладки «Настройки» (только «Модули»)
FEATURE_FLAG_KEYS: frozenset[str] = frozenset(
    {m.master for m in FEATURE_MODULES} | {s for m in FEATURE_MODULES for s in m.subs}
)


def _build_specs() -> dict[str, SettingSpec]:
    specs: dict[str, SettingSpec] = {}
    for key in SETTING_KEYS:
        kind = KEY_KINDS[key]
        if kind not in _SCALAR_KINDS:
            continue  # списки/словари — через отдельный UX (Шаг 6)
        lo, hi = _field_range(key)
        label, unit = _LABELS.get(key, (key, ""))
        specs[key] = SettingSpec(key, label, kind, lo, hi, unit)
    return specs


# ПОЛНЫЙ реестр скаляров (включая тумблеры модулей) — на нём держится
# парсинг/запись. Отображение фильтрует флаги: обычные настройки vs «Модули».
SETTING_SPECS: dict[str, SettingSpec] = _build_specs()
# тумблеры модулей отдельным срезом — для вкладки «Модули»
FEATURE_SPECS: dict[str, SettingSpec] = {
    k: SETTING_SPECS[k] for k in SETTING_SPECS if k in FEATURE_FLAG_KEYS
}


class GuildSettingsService(ISettingsProvider):
    def __init__(self, settings: Settings, session_factory: async_sessionmaker[AsyncSession]):
        self._settings = settings
        self._session_factory = session_factory
        # guild_id -> {key: parsed_value}
        self._cache: dict[int, dict[str, object]] = {}
        # guild_id -> собранная валидная модель (мемоизация; сбрасывается на set/reset)
        self._resolved: dict[int, GuildSettings] = {}

    async def load_all(self) -> None:
        """Поднять все переопределения в память (старт + ресинк листенера)."""
        self._cache = {}
        self._resolved = {}
        loaded = 0
        async with self._session_factory() as session:
            rows = (await session.execute(select(GuildSettingModel))).scalars().all()
        for row in rows:
            parsed = self._parse_row(row.key, row.value)
            if parsed is None:
                continue
            self._cache.setdefault(row.guild_id, {})[row.key] = parsed
            loaded += 1
        logger.info("Настройки серверов загружены: %d переопределений", loaded)

    async def reload_guild(self, guild_id: int) -> None:
        """Перечитать переопределения ОДНОЙ гильдии из БД. Реакция на NOTIFY от
        другого процесса (веб-панель записала настройку) — иначе in-memory кэш
        бота остаётся устаревшим до рестарта. Сбрасывает мемоизацию resolved."""
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(GuildSettingModel).where(GuildSettingModel.guild_id == guild_id)
                )
            ).scalars().all()
        parsed = {
            row.key: value
            for row in rows
            if (value := self._parse_row(row.key, row.value)) is not None
        }
        if parsed:
            self._cache[guild_id] = parsed
        else:
            self._cache.pop(guild_id, None)  # все переопределения сняты
        self._resolved.pop(guild_id, None)

    @staticmethod
    def _parse_row(key: str, raw: str):
        """Строка из БД -> типизированное значение (или None, если ключ вне
        реестра / значение битое). Общая логика load_all и reload_guild."""
        kind = KEY_KINDS.get(key)
        if kind is None:
            return None  # ключ убрали из реестра — игнор
        try:
            if kind in _COMPLEX_KINDS:
                return json.loads(raw)  # списки/словари — JSON
            return SETTING_SPECS[key].parse(raw)
        except (ValueError, json.JSONDecodeError):
            logger.warning("Некорректное значение настройки в БД", extra={"key": key})
            return None

    # --- чтение (синхронно, из кэша) ---

    def get(self, guild_id: int, key: str, default):
        return self._cache.get(guild_id, {}).get(key, default)

    def default(self, key: str):
        return getattr(self._settings, key)

    def current(self, guild_id: int, key: str):
        return self.get(guild_id, key, self.default(key))

    def is_override(self, guild_id: int, key: str) -> bool:
        return key in self._cache.get(guild_id, {})

    def overrides(self, guild_id: int) -> dict[str, object]:
        return dict(self._cache.get(guild_id, {}))

    def resolved(self, guild_id: int) -> GuildSettings:
        """Полная валидная модель настроек сервера: глобальные дефолты из .env,
        поверх — переопределения гильдии. Мемоизируется до следующей записи."""
        cached = self._resolved.get(guild_id)
        if cached is None:
            cached = self._build(self._cache.get(guild_id, {}))
            self._resolved[guild_id] = cached
        return cached

    # --- запись (в БД + кэш) ---

    async def set(self, guild_id: int, key: str, raw: str) -> int | float | str:
        """Валидирует и сохраняет переопределение. Возвращает разобранное
        значение; ValueError — если не прошло валидацию (поле или инвариант)."""
        spec = SETTING_SPECS[key]
        value = spec.parse(raw)
        # кросс-полевая валидация: собрать полную модель с этим переопределением
        candidate = {**self._cache.get(guild_id, {}), key: value}
        self._build(candidate)  # бросит ValueError на нарушении инварианта
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(GuildSettingModel).where(
                        GuildSettingModel.guild_id == guild_id,
                        GuildSettingModel.key == key,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(GuildSettingModel(guild_id=guild_id, key=key, value=str(value)))
            else:
                existing.value = str(value)
            await self._notify_change(session, guild_id)
            await session.commit()
        self._cache.setdefault(guild_id, {})[key] = value
        self._resolved.pop(guild_id, None)
        return value

    async def set_many(self, guild_id: int, values: dict[str, object]) -> None:
        """Атомарно задаёт несколько переопределений (в т.ч. списки/словари).
        Значения уже типизированы (list[int], list[str], dict...). Полная модель
        валидируется целиком ОДИН раз — так связанные ключи (пороги ↔ имена
        ролей) меняются согласованно; ValueError — если инвариант нарушен и
        тогда ничего не сохраняется."""
        merged = {**self._cache.get(guild_id, {}), **values}
        self._build(merged)  # бросит ValueError на нарушении инварианта
        async with self._session_factory() as session:
            for key, value in values.items():
                existing = (
                    await session.execute(
                        select(GuildSettingModel).where(
                            GuildSettingModel.guild_id == guild_id,
                            GuildSettingModel.key == key,
                        )
                    )
                ).scalar_one_or_none()
                raw = _serialize(key, value)
                if existing is None:
                    session.add(GuildSettingModel(guild_id=guild_id, key=key, value=raw))
                else:
                    existing.value = raw
            await self._notify_change(session, guild_id)
            await session.commit()
        self._cache.setdefault(guild_id, {}).update(values)
        self._resolved.pop(guild_id, None)

    async def reset(self, guild_id: int, key: str) -> bool:
        """Убирает переопределение — вернётся глобальный дефолт. False — его и не было."""
        async with self._session_factory() as session:
            result = await session.execute(
                delete(GuildSettingModel).where(
                    GuildSettingModel.guild_id == guild_id,
                    GuildSettingModel.key == key,
                )
            )
            if result.rowcount > 0:
                await self._notify_change(session, guild_id)
            await session.commit()
        self._cache.get(guild_id, {}).pop(key, None)
        self._resolved.pop(guild_id, None)
        return result.rowcount > 0

    # --- внутреннее ---

    @staticmethod
    async def _notify_change(session: AsyncSession, guild_id: int) -> None:
        """Транзакционный pg_notify: другой процесс (веб-панель, шард) перечитает
        кэш этой гильдии. Внутри транзакции -> доставится на COMMIT, отменится
        на rollback. Только Postgres; на SQLite молча пропускаем (один писатель).
        pg_notify(func) вместо `NOTIFY` — payload как bind-параметр, без инъекций."""
        bind = session.bind
        if bind is None or bind.dialect.name != "postgresql":
            return
        await session.execute(
            text("SELECT pg_notify(:channel, :payload)"),
            {"channel": SETTINGS_NOTIFY_CHANNEL, "payload": str(guild_id)},
        )

    def _build(self, overrides: dict[str, object]) -> GuildSettings:
        """Собрать GuildSettings: база из глобального Settings, поверх — overrides.
        ValidationError переводится в ValueError с понятным текстом."""
        base = {k: getattr(self._settings, k) for k in SETTING_KEYS}
        try:
            return GuildSettings(**{**base, **overrides})
        except ValidationError as exc:
            raise ValueError(_first_error(exc)) from None


def _serialize(key: str, value: object) -> str:
    """Значение -> строка для БД: скаляры как str(), списки/словари как JSON."""
    if KEY_KINDS.get(key) in _COMPLEX_KINDS:
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _first_error(exc: ValidationError) -> str:
    """Первое сообщение из ValidationError без служебного префикса pydantic."""
    errors = exc.errors()
    if not errors:
        return "недопустимое значение"
    msg = str(errors[0].get("msg", "недопустимое значение"))
    return msg.removeprefix("Value error, ")
