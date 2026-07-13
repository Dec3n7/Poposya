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
from sqlalchemy import delete, select
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
_SCALAR_KINDS = frozenset({"int", "float", "bool", "channel"})
_COMPLEX_KINDS = frozenset({"list", "dict"})

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
    # активность
    "voice_points_per_hour": ("Очки за час в войсе (0=выкл)", ""),
    "lonely_hours": ("Часов тишины до «скучаю»", "ч"),
    "absent_days_threshold": ("Дней отсутствия до «с возвращением»", "дн"),
    # киноклуб
    "cinema_rating_hours": ("Сбор оценок после просмотра", "ч"),
    "cinema_watchlist_max": ("Предел вотчлиста", ""),
    "cinema_forum_channel": ("Форум «золотой фонд» (0=выкл)", ""),
    # находки
    "finds_channel_id": ("Канал находок (0 = по имени / MAIN)", ""),
    "finds_min_interval_hours": ("Мин. интервал находок", "ч"),
    "finds_max_interval_hours": ("Макс. интервал находок", "ч"),
    "finds_fail_penalty": ("Штраф за провал похода", "очк"),
    "finds_claim_cooldown_hours": ("Кулдаун похода", "ч"),
    # музыка
    "music_karaoke_ansi": ("Цветное караоке (ANSI)", ""),
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
    kind: str  # "int" | "float" | "channel" | "bool"
    min: int | float | None = None
    max: int | float | None = None
    unit: str = ""  # для подсказки: «ч», «мин», «сек», «очк»…

    def parse(self, raw: str) -> int | float:
        """Текст из команды -> валидное значение или ValueError с понятным текстом."""
        raw = raw.strip()
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


# ключ -> спека; порядок как в модели (модерация → отношения → AI → …)
SETTING_SPECS: dict[str, SettingSpec] = _build_specs()


class GuildSettingsService(ISettingsProvider):
    def __init__(self, settings: Settings, session_factory: async_sessionmaker[AsyncSession]):
        self._settings = settings
        self._session_factory = session_factory
        # guild_id -> {key: parsed_value}
        self._cache: dict[int, dict[str, object]] = {}
        # guild_id -> собранная валидная модель (мемоизация; сбрасывается на set/reset)
        self._resolved: dict[int, GuildSettings] = {}

    async def load_all(self) -> None:
        """Поднять все переопределения в память (вызывается при старте)."""
        self._cache = {}
        self._resolved = {}
        loaded = 0
        async with self._session_factory() as session:
            rows = (await session.execute(select(GuildSettingModel))).scalars().all()
        for row in rows:
            kind = KEY_KINDS.get(row.key)
            if kind is None:
                continue  # ключ убрали из реестра — игнор
            try:
                if kind in _COMPLEX_KINDS:
                    value = json.loads(row.value)  # списки/словари — JSON
                else:
                    value = SETTING_SPECS[row.key].parse(row.value)
            except (ValueError, json.JSONDecodeError):
                logger.warning("Некорректное значение настройки в БД", extra={"key": row.key})
                continue
            self._cache.setdefault(row.guild_id, {})[row.key] = value
            loaded += 1
        logger.info("Настройки серверов загружены: %d переопределений", loaded)

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

    async def set(self, guild_id: int, key: str, raw: str) -> int | float:
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
            await session.commit()
        self._cache.get(guild_id, {}).pop(key, None)
        self._resolved.pop(guild_id, None)
        return result.rowcount > 0

    # --- внутреннее ---

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
