"""Пер-гильдийные настройки: реестр редактируемых ключей + сервис.

Переопределения сервера лежат в таблице guild_settings и целиком грузятся
в память при старте (load_all). Чтение (get/current) — синхронное, из кэша,
без похода в БД: годится для горячих путей (антиспам, циклы). Запись
(set/reset) идёт в БД и обновляет кэш.

Что редактируемо — задаёт SETTING_SPECS; изменить через /config можно только
эти ключи, секреты (токен, ключи API, БД) сюда не входят по определению."""
import logging
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.interfaces.settings_provider import ISettingsProvider
from src.config import Settings
from src.infrastructure.db.models.guild import GuildSettingModel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    kind: str          # "int" | "channel"
    min: int | None = None
    max: int | None = None
    unit: str = ""     # для подсказки: «ч», «мин», «сек», «очков»…

    def parse(self, raw: str) -> int:
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
        try:
            value = int(raw)
        except ValueError:
            raise ValueError("нужно целое число")
        if self.min is not None and value < self.min:
            raise ValueError(f"минимум {self.min}")
        if self.max is not None and value > self.max:
            raise ValueError(f"максимум {self.max}")
        return value


_SPECS: list[SettingSpec] = [
    # --- модерация ---
    SettingSpec("warn_threshold", "Варнов до мута", "int", 1, 20),
    SettingSpec("warn_mute_minutes", "Длительность мута по варнам", "int", 1, 40320, "мин"),
    SettingSpec("spam_limit", "Сообщений за окно = спам", "int", 2, 50),
    SettingSpec("spam_window", "Окно антиспама", "int", 3, 120, "сек"),
    SettingSpec("spam_mute_minutes", "Мут за спам", "int", 1, 1440, "мин"),
    # --- киноклуб ---
    SettingSpec("cinema_rating_hours", "Сбор оценок после просмотра", "int", 1, 336, "ч"),
    SettingSpec("cinema_watchlist_max", "Предел вотчлиста", "int", 5, 500),
    SettingSpec("cinema_forum_channel", "Форум «золотой фонд» (0=выкл)", "channel"),
    # --- находки ---
    SettingSpec("finds_channel_id", "Канал находок (0 = по имени / MAIN)", "channel"),
    SettingSpec("finds_min_interval_hours", "Мин. интервал находок", "int", 1, 336, "ч"),
    SettingSpec("finds_max_interval_hours", "Макс. интервал находок", "int", 1, 720, "ч"),
    SettingSpec("finds_fail_penalty", "Штраф за провал похода", "int", 0, 500, "очков"),
    SettingSpec("finds_claim_cooldown_hours", "Кулдаун похода", "int", 0, 168, "ч"),
    # --- очки и активность ---
    SettingSpec("voice_points_per_hour", "Очки за час в войсе (0=выкл)", "int", 0, 100),
    SettingSpec("relationship_daily_point_cap", "Дневной потолок очков", "int", 1, 5000),
    # --- музыка ---
    SettingSpec("music_karaoke_ansi", "Цветное караоке (ANSI)", "bool"),
    SettingSpec("lonely_hours", "Часов тишины до «скучаю»", "int", 1, 168, "ч"),
    SettingSpec("absent_days_threshold", "Дней отсутствия до «с возвращением»", "int", 1, 365, "дн"),
]

SETTING_SPECS: dict[str, SettingSpec] = {s.key: s for s in _SPECS}


class GuildSettingsService(ISettingsProvider):
    def __init__(self, settings: Settings, session_factory: async_sessionmaker[AsyncSession]):
        self._settings = settings
        self._session_factory = session_factory
        # guild_id -> {key: parsed_value}
        self._cache: dict[int, dict[str, int]] = {}

    async def load_all(self) -> None:
        """Поднять все переопределения в память (вызывается при старте)."""
        self._cache = {}
        loaded = 0
        async with self._session_factory() as session:
            rows = (await session.execute(select(GuildSettingModel))).scalars().all()
        for row in rows:
            spec = SETTING_SPECS.get(row.key)
            if spec is None:
                continue  # ключ убрали из реестра — игнор
            try:
                value = spec.parse(row.value)
            except ValueError:
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

    def overrides(self, guild_id: int) -> dict[str, int]:
        return dict(self._cache.get(guild_id, {}))

    # --- запись (в БД + кэш) ---

    async def set(self, guild_id: int, key: str, raw: str) -> int:
        """Валидирует и сохраняет переопределение. Возвращает разобранное
        значение; ValueError — если не прошло валидацию."""
        spec = SETTING_SPECS[key]
        value = spec.parse(raw)
        async with self._session_factory() as session:
            existing = (await session.execute(
                select(GuildSettingModel).where(
                    GuildSettingModel.guild_id == guild_id,
                    GuildSettingModel.key == key,
                )
            )).scalar_one_or_none()
            if existing is None:
                session.add(GuildSettingModel(guild_id=guild_id, key=key, value=str(value)))
            else:
                existing.value = str(value)
            await session.commit()
        self._cache.setdefault(guild_id, {})[key] = value
        return value

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
        return result.rowcount > 0
