# Подготовка к монетизации (tier / entitlement)

> Статус: **подготовка**, не реализация. Здесь — карта груд­ворка, который
> делает будущий tier-рефакторинг маленькой вставкой, а не переписыванием.
> Продуктовая логика тарифов — в `MONETIZATION_IDEAS.md` (корень репо).
>
> **Принцип:** строим *швы* сейчас, *фичу* — потом. Ни один шаг ниже не включает
> монетизацию и не меняет поведение бота. Мы лишь сводим точки чтения настроек и
> гейтинга к местам, куда позже вставляется tier-кламп и entitlement.

## 1. Blast radius (замерено по коду)

| Что | Кол-во | Где |
|---|---|---|
| Чтения настроек `_cfg(guild_id, key, default)` | **59** | application-слой (сервисы, use-cases) |
| Чтения настроек `gs.get(guild_id, key, default)` | **29** | коги |
| Атрибутные чтения `gs.<field>` (3-й паттерн) | ~несколько | see §2 |
| Гейт `flag_on(...)` | **13** | коги |
| Гейт `block_if_module_off(...)` | **13** | `interaction_check` когов |
| Коги с `interaction_check` | **15** | `infrastructure/discord/cogs` |
| Файлов, читающих настройки | **24** | app + cogs |

Вывод: **~88 чтений значений** и **~26 гейт-точек**. Без единого шва tier-кламп
пришлось бы вставлять во все 88 мест. Задача подготовки — свести это к **одной
функции-резолверу** и **одному гейт-хелперу**.

## 2. Паттерны чтения настроек — резолвер обязан накрыть все три

1. `self._cfg(guild_id, key, default)` — метод сервисов app-слоя
   (напр. `application/ai_chat/service.py`). Основной путь (59 сайтов).
2. `gs.get(guild_id, key, default)` — провайдер пер-серверных настроек в когах
   (29 сайтов).
3. **Атрибутный доступ на собранном объекте** — напр.
   `application/relationship/use_cases.py`: `gs.relationship_daily_point_cap`.
   Не проходит ни через `_cfg`, ни через `gs.get` — резолвер-обёртка его не
   перехватит, если про него забыть.

### Ловушки

- **Коллизия имени `gs`.** Так зовут и провайдер `GuildSettings`, и глобальный
  `Settings` из `.env`. Нельзя механически трактовать любой `gs.<field>` как
  пер-серверную настройку — секреты/инфра (`gs.discord_token`,
  `gs.spotify_client_id`, `gs.warden_api_token`, `gs.web_allowed_origin`) это
  **глобальный** объект и тарификации не подлежат никогда.
- **Часть лимитов — только глобальные, их нет в `GuildSettings`.**
  `music_playlist_max_per_guild`, `music_playlist_limit`, `music_prefetch_tracks`,
  `music_cache_max_mb` читаются как `gs.<field>` из глобального `Settings`, а в
  пер-серверной схеме (`application/guild_config/schema.py`) их **нет**. Чтобы
  тарифицировать музыкальные лимиты — их сперва надо *перенести* в
  `GuildSettings` (это Prep 3, а не «поменять дефолт»).
- **Лимита лайков музыки не существует вовсе** (ни глоб., ни пер-серверно) —
  сейчас коллекция без потолка. Для §B из идей его надо *завести* (Prep 3).

## 3. Классификация ключей: что тарифицируется, что нет

Три категории. Enforcement нигде пока не пишем — это только карта решений.

- **MODULE** — мастер-тумблер «фича есть/нет». Для тарифа → *гейт* (`require_tier`
  поверх `flag_on`): free-тариф не может включить Premium-модуль.
- **CAP** — числовой лимит, различается по тарифу. Для тарифа → *кламп*
  `min(настроенное_админом, потолок_тарифа)`.
- **CONFIG** — чистая серверная кастомизация (каналы, имена/цвета ролей, пороги
  баланса). Тарифом **не трогается никогда** — админ волен настраивать.

### 3.1 CAP — кандидаты на кламп по тарифу

| Ключ | Где живёт | Free-потолок (предложение) | Примечание |
|---|---|---|---|
| `ai_rate_limits_by_level` | GuildSettings | низкий cap/уровень | **главный paywall** — AI-реплики/сутки |
| `ai_dialog_summary_keep` | GuildSettings | 0–1 | AI-память (Free выкл/1 строка) |
| `ai_context_messages` | GuildSettings | короткий | глубина памяти |
| `relationship_notes_max_chars` | GuildSettings | урезано | заметки Попоси |
| `tempvoice_max_per_guild` | GuildSettings | 3–5 | каморки |
| `cinema_watchlist_max` | GuildSettings | меньше | вотчлист |
| `finds_min/max_interval_hours` | GuildSettings | реже спавн | находки |
| `finds_claim_cooldown_hours` | GuildSettings | длиннее кд | находки |
| `autorole_ids` (длина) | GuildSettings | 1 роль | автовыдача новичку |
| `music_playlist_max_per_guild` | **глоб. Settings** ⚠️ | — | сперва перенести в GuildSettings |
| `music_liked_max` | **не существует** ⚠️ | 20 | сперва завести поле |
| `remind` active cap | **не существует** ⚠️ | 3 | напоминания сейчас без лимита — завести поле + enforce в cog (отложено) |
| `send_per_hour` | GuildSettings ✅ | 2 | **сделано (Prep 3)**: перенесён в схему, читается через шов, в TIERABLE |

### 3.2 MODULE — гейт по тарифу (Premium-only фичи из §12)

| Ключ-тумблер | Тариф-минимум |
|---|---|
| `git_enabled` (`/git`) | Premium |
| `steam_enabled` (`/steam`) | Premium |
| `digest_enabled` | Premium |
| `achievements_enabled` | Premium |
| `secret_room_enabled` | Premium |
| `activity_album` (Альбом) | Premium |
| `staykick_enabled` (24/7-подобное) | Pro |

Остальные тумблеры (`moderation_*`, `ai_chat_*`, `music_enabled`,
`relationship_*`, `fun_enabled`, `appeals_enabled`, `banwatch_enabled`,
`introduce_enabled`, `tempvoice_*`, `finds_enabled`, `cinema_enabled`,
`activity_*`) — **free-ядро**, тарифом не гейтятся.

### 3.3 CONFIG — никогда не тарифицируется

Все `*_channel*` / `*_category`, `*_role*`/`*_role_names`, пороги баланса
(`warn_*`, `spam_*`, `relationship_role_thresholds`, `*_decay_*`,
`voice_points_per_hour`, `holiday_points_multiplier`, `birthday_remind_days`,
`ai_dialog_gap_minutes`, `ai_event_comment_*` …). Это кастомизация сервера.

### 3.4 Неприкосновенное (навсегда free — фиксируем в Terms)

Core-модерация, appeals, `/forgetme`, `/checkuser` для наказанного, `/rules`,
health, возможность выключить любой модуль. Не CAP и не MODULE в тарифном смысле
— просто не подлежит монетизации по репутационным соображениям.

## 4. Чек-лист подготовки (порядок по рычагу)

Статус: **все швы установлены, поведение не изменено** (заглушка тарифов выдаёт
PRO → кламп и гейт — no-op). Полный тест-сет зелёный (1862 passed).

- [x] **Prep 0 · Карта** — этот документ. Blast radius + 3 паттерна + ловушки +
      классификация ключей.
- [x] **Prep 1 · Единый шов над провайдером.** Оказалось, резолвер уже единый:
      все `_cfg`/`gs.get`/`flag_on` сходятся в `ISettingsProvider.get`
      (`GuildSettingsService.get`). Поэтому вместо слияния 88 сайтов —
      декоратор `TierClampSettingsProvider` (`infrastructure/tier_clamp.py`),
      обёрнутый вокруг сервиса в `root_container` (`container.guild_settings` и
      все `settings_provider=`). Читает через кламп, пишет — делегирует.
      Ограничение: атрибутный путь `resolved(gid).<field>` не клампится
      (тарифные лимиты читать через `get`). Тесты: `tests/test_tier_clamp.py`.
- [x] **Prep 2 · Реестр тарифных ключей.** `TIERABLE` (+ `TierCap`, `ClampDir`,
      `TIER_NEVER`) в `application/guild_config/schema.py` — только данные.
      Parity-тесты в `tests/test_guild_config_schema.py`.
- [~] **Prep 3 · Недостающие CAP-ручки.** Сделано: `send_per_hour` перенесён из
      глоб. `Settings` в `GuildSettings` (читается через шов, добавлен в
      `TIERABLE`) — поведение то же, но стал per-guild и тарифицируемым.
      **Отложено** (не плодим мёртвые настройки в /config до enforcement):
      `music_liked_max` (лимита лайков нет вовсе), активный лимит `/remind`
      (тоже нет), перенос музыкальных playlist-лимитов из глоб. `Settings`.
- [x] **Prep 4 · Порт `IEntitlements` + заглушка.** `PlanTier(FREE/PREMIUM/PRO)`
      и `IEntitlements` в `application/interfaces/entitlements.py`;
      `UnlimitedEntitlements` (всем PRO) в `infrastructure/entitlements.py`;
      проведён через `root_container`. Свап на БД-реализацию = 1 строка.
- [x] **Prep 5 · Гейт `require_tier`** рядом с `block_if_module_off` в
      `feature_flags.py` + карта `MODULE_MIN_TIER` в `schema.py`. Сегодня
      пускает всех. Тесты: `tests/test_feature_flags_tier.py`.
- [x] **Prep 6 · Инвентарь stateful-фич для downgrade/grace** — см. §5.

## 5. Инвентарь stateful-фич для downgrade/grace (Prep 6)

При понижении тарифа (истёк Premium, отменена оплата) нельзя резко «вырывать»
уже открытое/запущенное — это ломает доверие. Ниже фичи, держащие состояние во
времени, и где живёт их жизненный цикл. Логика downgrade должна выбрать
**мягкое завершение**: дать доиграть/дожить срок, но не продлевать/пересоздавать.

| Фича | Состояние | Где в коде | Политика при downgrade (предложение) |
|---|---|---|---|
| Secret room | открытый текст+войс канал со сроком в БД | `application/relationship/use_cases.py` (`RegisterSecretRoomUseCase`, `PopExpiredSecretRoomsUseCase`); срок `secret_room_hours` | дать дожить срок; новый ключ не выдавать |
| Каморки (tempvoice) | живые каналы + реестр в БД | `infrastructure/db/repositories/tempvoice.py`, ког `cogs/tempvoice/` | не удалять существующие; блокировать создание сверх free-лимита |
| 24/7 / staykick-сессия | активная привязка к каналу | `application/staykick/use_cases.py` | завершить по текущему сроку; не запускать новые |
| Музыка: радио/сессия плеера | in-memory состояние авто-подбора | `cogs/music/radio.py`, `cogs/music/service.py` | доиграть очередь; радио-подхват выключить |
| Tempban / mute со сроком | срок в БД, авто-снятие | `application/moderation` (`PopExpiredBansUseCase`) | **не трогать** — это модерация, не тариф |

Общий принцип: downgrade **гасит создание нового**, а не отбирает активное.
Технически удобно повесить на то же событие «entitlement изменился» (§7): при
переходе в FREE — пройтись по этому списку и пометить «не продлевать».

## 6. Осознанно НЕ готовим сейчас

Реальные значения тарифов · сам кламп-enforcement · интеграция оплаты
(Boosty/FunPay/Discord SKU) · downgrade-логика · UI-бейджи. Это дорогие/внешние/
необратимые части — откладываем намеренно. Подготовка касается только швов.

## 7. Как это стыкуется с существующим мостом

Синк «оплата → tier» переиспользует уже готовый мост панель↔бот на
`LISTEN/NOTIFY`: событие «entitlement изменился» идёт тем же путём, что настройки/
персоны. Отдельный inbound-механизм не нужен.

## 8. РЕАЛИЗОВАНО: ручная выдача подписки через панель

Поверх подготовленных швов построена рабочая система тарифов:

- **Таблица** `guild_entitlements` (миграция 0040): `guild_id, tier, expires_at,
  granted_by, updated_at`. Одна строка на сервер; нет строки = тариф по
  умолчанию.
- **`EntitlementService`** (`infrastructure/entitlements.py`) — как
  `GuildSettingsService`: кэш в памяти + `load_all/reload_guild`, чтение `tier()`
  учитывает срок (истёк → тариф по умолчанию), запись `grant/revoke` + pg_notify.
- **Листенер** `entitlements_listener.py` (канал `poposya_entitlements`) — бот и
  панель видят выдачу без рестарта. Проведён в DI бота и API, стартует в `main`
  и в lifespan API.
- **API** (`api/routers/entitlements.py`, только оператор — `require_operator`):
  `GET/PUT/DELETE /api/guilds/{guild_id}/entitlement`. PUT принимает
  `{tier, duration_days}` (null/0 = бессрочно). Аудит `entitlement.*`.
- **Панель**: вкладка **«Подписка»** (`web/src/components/Subscription.tsx`),
  видна только оператору. Показывает текущий тариф/срок, даёт выдать
  (тариф + пресет срока) и снять.
- **Enforcement-переключатель** `ENTITLEMENTS_DEFAULT_TIER` (config,
  **по умолчанию `free`**):
  - **`free`** (дефолт) — enforcement ВКЛ: серверы без подписки получают Free
    (лимиты клампятся автоматически через шов; Premium-**модули** — через
    `require_tier`, когда он навешен на коги), а выданные вручную подписки
    работают до срока.
  - **`pro`** — enforcement ВЫКЛ: все серверы получают максимум, поведение как
    раньше. Временный «рубильник», если нужно быстро отключить платность.

### Как это работает сейчас (дефолт free)

1. Задать `WEB_OPERATOR_IDS` (Discord-ID владельца) — иначе вкладка «Подписка»
   и API недоступны.
2. Выдавать подписки серверам в панели (вкладка «Подписка»): тариф + срок N.
3. Серверы без подписки уже работают на Free — их лимиты (cap'ы) клампятся.
   Чтобы временно вернуть всем максимум — `ENTITLEMENTS_DEFAULT_TIER=pro`.

### Enforcement Premium-МОДУЛЕЙ — навешено

- **Команды** (`/git`, `/steam`, `/digest`, `/achievements`, `/secret`) — гейт
  `require_tier` в `interaction_check` соответствующих когов: на тарифе ниже
  требуемого команда отвечает эфемерным «доступно на платном тарифе».
- **Событийные модули**: `staykick` (Pro) — в `on_member_join` через
  `tier_allows` (новых кандидатов на кик не заводим; уже запланированные
  дорабатывают — grace); «Альбом» (Premium) — в обработчике реакции через
  `tier_allows` (на free тихо не постим).
- `entitlements` проброшен во все эти коги из контейнера в `client.py`. Карта
  порогов — `MODULE_MIN_TIER`; помощники — `require_tier` (команды) и
  `tier_allows` (события) в `feature_flags.py`.

Итог: при `ENTITLEMENTS_DEFAULT_TIER=free` сервер без подписки получает Free —
лимиты клампятся, Premium-модули закрыты; выданная подписка открывает их на срок.

### Синхронизация нескалярных cap'ов (AI-лимитер)

Шов клампит и нескалярные лимиты на free:
- **`ai_rate_limits_by_level`** (главный AI-paywall) — `dict_per_level`: каждый
  уровень зажимается потолком (≤10 реплик/час на free). `ai_chat` читает лимиты
  через провайдер-шов, поэтому кламп применяется **без правок в самом ai_chat**.
- **`autorole_ids`** — `list_length`: на free берётся только первая автороль.

**Музыка** (очередь/лайки/плейлисты) намеренно **не тарифицируется** cap'ами:
по §B музыка — бесплатный бонус, а не headline-paywall. Лимит длины очереди как
per-guild-настройки не существует; вводить его в муз-сабсистему ради «бонуса» не
стали. Если позже понадобится — завести `music_queue_max` в `GuildSettings`,
добавить в `TIERABLE` и читать в `enqueue_tracks` через провайдер (тот же шов).
