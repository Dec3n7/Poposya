# План: пер-серверные настройки (guild settings)

Этап 1 большой задачи «админ-панель по серверам». Цель этапа — научить бота
хранить и применять настройки отдельно для каждой гильдии, с валидацией и
аудитом. UI (слеш-команды, затем веб-панель) строится поверх и бизнес-код
больше не трогает.

Статус: **запланировано, не начато** (план от 2026-07-11).

---

## Предусловие: git init

Рефакторинг тронет ~25 use case'ов и почти все коги. Перед началом —
`git init` и первый коммит текущего рабочего состояния. Дополнить
`.gitignore`: `poposya.db`, `logs/`, каталог бэкапов.

---

## Шаг 0. Граница: что настраивается по серверам, а что нет

Принцип: **поведение фич — per-guild, инфраструктура и секреты — только `.env`**.

Остаются глобальными: `discord_token`, все `log_*`, `health_port`,
`database_url`, `auto_migrate`, `backup_*`, `outbox_*`, все API-ключи
(Groq/TMDB/Kinopoisk/Spotify), инфраструктурные параметры AI (retry, circuit
breaker, таймауты, семафор), `ffmpeg_path`, `ytdlp_*`, `dev_guild_id`.

Per-guild — всё остальное из `src/config.py`: каналы (`main_channel`,
`welcome_channel`, `log_channel`, `album_*`, `finds_channel`), модерация
(`warn_*`, `spam_*`, `auto_role`), отношения (пороги, имена ролей, cap, decay,
secret room), поведение AI (`ai_context_messages`, `ai_event_comment_*`,
`ai_rate_limits_by_level`, диалоговые параметры), музыка (лимиты, громкость,
idle), находки, киноклуб, праздники, дни рождения.

Разделение фиксируется типом (шаг 1), а не документацией: чего нет в модели —
то нельзя переопределить в принципе.

## Шаг 1. Модель `GuildSettings` — единственный источник правды

Новый файл `src/application/guild_config/schema.py`: pydantic-модель со
**всеми** per-guild ключами, их типами, диапазонами и дефолтами:

```python
class GuildSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    warn_threshold: int = Field(3, ge=1, le=20)
    relationship_daily_point_cap: int = Field(20, ge=1, le=1000)
    relationship_role_thresholds: list[int] = [100, 250, 450, 700, 950, 1200]
    relationship_role_names: list[str] = [...]
    ...

    @model_validator(mode="after")
    def _roles_match_thresholds(self):
        # имён ролей ровно len(thresholds) + 1 (эксклюзив);
        # thresholds строго возрастают; exclusive_threshold > последнего порога
        ...
```

Почему pydantic, а не dataclass: валидация диапазонов и кросс-полевых
инвариантов (пороги ↔ имена ролей) бесплатно, и эта же модель потом станет
схемой формы для `/settings` и веб-панели. Дефолты модели дублируют
`Settings` — чтобы не разъехались, добавляется тест-сверка поле-в-поле
(шаг 5). Глобальные значения из `.env` продолжают работать как base-дефолты
для всех гильдий.

Удобные хелперы прямо на модели: `points_policy()` → `PointsToLevelPolicy`,
`holiday_calendar()` → `HolidayCalendar` (оба — дешёвые frozen-объекты,
строятся по требованию).

## Шаг 2. Хранение: key-value, а не широкая таблица

Миграция `0012_guild_settings` (по образцу `0011_liked_tracks.py`):

```
guild_settings(
  guild_id   BigInteger  NOT NULL,
  key        Text        NOT NULL,
  value      Text        NOT NULL,   -- JSON-сериализованное значение
  updated_at DateTime    NOT NULL,
  updated_by BigInteger  NOT NULL,   -- кто менял (аудит)
  PRIMARY KEY (guild_id, key)
)
```

Обоснование key-value: новая настройка не требует миграции; строки с
неизвестными ключами (после отката версии бота) игнорируются с warning'ом,
а не роняют старт. JSON в `Text` переносим на PostgreSQL без правок.
Хранятся **только переопределения**: сброс настройки = `DELETE`, после
которого снова действует дефолт.

Модель + `SqlAlchemyGuildSettingsRepository` (`load_all(guild_id)`,
`upsert(...)`, `delete(guild_id, key)`) — по образцу существующих
репозиториев; upsert диалект-независимый, как в voice progress.

## Шаг 3. Порт и сервис с кэшем

Порт в `src/application/interfaces/guild_config.py`:

```python
class IGuildConfig(ABC):
    async def get(self, guild_id: int) -> GuildSettings: ...          # дефолты ⊕ оверрайды
    async def set(self, guild_id: int, key: str, value: Any, updated_by: int) -> GuildSettings: ...
    async def reset(self, guild_id: int, key: str, updated_by: int) -> GuildSettings: ...
    async def overrides(self, guild_id: int) -> dict[str, Any]: ...   # для UI: что переопределено
```

Реализация `GuildConfigService` в infrastructure: ленивая загрузка по
гильдии, кэш `dict[int, GuildSettings]` в памяти, инвалидация при
`set`/`reset`, per-guild `asyncio.Lock` против гонки первой загрузки.
Кэш обязателен: `get()` дёргается на каждое сообщение (AI, антиспам, очки).

Известное ограничение (зафиксировать комментарием): инвалидация только
внутрипроцессная — при переезде на несколько процессов потребуется
пересмотр (TTL или pub/sub).

Целостность: `set` валидирует **весь** эффективный конфиг
(`GuildSettings(**{**defaults, **overrides, key: value})`), а не одно поле —
иначе серией одиночных валидных шагов можно привести пару порогов/имён
в невалидное состояние.

## Шаг 4. Рефакторинг потребителей — пофичево, каждый шаг зелёный

Два паттерна потребления настроек:

**(а) Запечённые в конструкторы use case'ов** (~25 штук в
`src/application/di/root_container.py`): скалярные параметры заменяются на
инъекцию порта, значение резолвится в `execute()` по `guild_id` (он там
уже везде есть):

```python
class WarnUserUseCase:
    def __init__(self, uow_factory: UowFactory, config: IGuildConfig): ...
    async def execute(self, ..., guild_id: int, ...):
        cfg = await self._config.get(guild_id)   # один раз на execute —
        threshold = cfg.warn_threshold            # консистентно внутри транзакции
```

Особые случаи (сейчас создаются один раз на процесс и шарятся между фичами):
- `PointsToLevelPolicy` — строить по требованию из `cfg.points_policy()`.
- `ChatService` — принимает `role_names`, `rate_limits_by_level`, `calendar`,
  диалоговые параметры конструктором; переводится на порт, `guild_id` уже
  есть в `ChatRequest`.

**(б) Чтение `self.settings.x` в когах на месте вызова** (~50 мест в слое
discord): behavioral-ключи меняются на `cfg = await self.config.get(guild_id)`;
инфраструктурные (`ffmpeg_path`, API-ключи, `dev_guild_id`) остаются как есть.

Порядок миграции — фича за фичей, каждая отдельным коммитом с зелёными
тестами: **moderation** (самая маленькая, обкатка паттерна) → relationship →
ai_chat → music → finds → cinema → activity. До конца миграции обе схемы
сосуществуют без конфликта: непереведённая фича продолжает читать глобальный
`Settings`.

## Шаг 5. Тесты

- **Схема**: диапазоны, кросс-валидация порогов/имён, отклонение неизвестных ключей.
- **Дефолты не разъехались**: сверка дефолтов `GuildSettings` со значениями
  `Settings()` по общим полям.
- **Merge-семантика**: `set` → значение видно; `reset` → вернулся дефолт;
  невалидный `set` → `ValidationError`, оверрайды не тронуты.
- **Репозиторий**: roundtrip upsert/delete на aiosqlite (паттерн есть в
  `tests/test_tech_debt.py`).
- **Кэш**: после `set` следующий `get` отдаёт новое значение; параллельные
  первые `get` не дублируют загрузку.
- **Существующие тесты use case'ов**: фейк `StaticGuildConfig(GuildSettings(...))`
  вместо нынешних скалярных аргументов.
- **Регрессия поведения**: с нулём оверрайдов бот ведёт себя байт-в-байт как сейчас.

## Шаг 6. Первый интерфейс — `/settings` в Discord

Тонкий ког поверх порта, без своей логики:

- `/settings show [раздел]` — эффективные значения, переопределённые помечены;
- `/settings set key value` / `/settings reset key` — только админ;
- ошибки валидации pydantic показываются человеку как есть;
- изменения логируются в `log_channel` (кто, что, старое → новое;
  `updated_by` уже в схеме).

Проверяет весь стек боем и остаётся навсегда как fallback. Веб-панель
(этап 3 общей задачи) сядет на тот же `IGuildConfig` и ту же модель —
бизнес-код больше не трогается.

---

## Объём и порядок выполнения

1 миграция, ~6 новых файлов, правки в ~25 use case'ах и ~10 когах + тесты.

1. `git init` + первый коммит.
2. Шаги 1–3 одним заходом (самодостаточны, ничего не ломают) + тесты шага 5
   на схему/репозиторий/кэш.
3. Шаг 4: moderation как образец → остальные фичи по одной.
4. Шаг 6: ког `/settings`.

Самый трудоёмкий кусок — шаг 4 (механический); самые важные для качества —
шаги 1 и 3 (валидация целиком + кэш с инвалидацией).

## Этапы после этого плана (контекст)

- **Этап 2**: `/settings` уже покрывает 80% пользы — пауза, обкатка.
- **Этап 3**: веб-панель — aiohttp в том же процессе (рядом с `/health`),
  вход через Discord OAuth2 (`identify guilds`), показывать только серверы,
  где у пользователя Manage Guild и стоит бот; сессии с подписанной кукой,
  CSRF на формы записи, проверка прав на каждый запрос записи; валидация —
  та же модель `GuildSettings`.
