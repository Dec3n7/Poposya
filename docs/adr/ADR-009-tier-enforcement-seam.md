# ADR-009 — Enforcement тарифов через единый шов clamp/gate

- **Статус:** Accepted (2026-08)
- **Связано:** [ADR-008](ADR-008-per-guild-settings-provider.md), [ADR-010](ADR-010-panel-bot-listen-notify.md), [ADR-014](ADR-014-entitlement-grant-and-billing.md), план-первоисточник — [monetization-prep](../plans/monetization-prep.md)

## Контекст

Монетизация требует различать возможности по тарифам (Free / Premium / Pro):
зажимать лимиты на Free и закрывать Premium-модули. Blast radius велик — ~88
чтений настроек и ~26 гейт-точек. Вставлять `if premium:` в каждое из них —
верный путь к рассинхрону и забытым местам. Нужен **один шов**, а не 88 правок.

## Решение

Тариф — отдельный порт `IEntitlements` / `PlanTier`; enforcement навешивается
двумя механизмами поверх уже единой точки чтения настроек
([ADR-008](ADR-008-per-guild-settings-provider.md)):

- **CAP-лимиты — кламп** через декоратор `TierClampSettingsProvider` над
  `ISettingsProvider`. Реестр тарифицируемых ключей — `TIERABLE` (только данные:
  `free_limit`, направление, спец-тип). На Free значение зажимается потолком —
  включая нескалярные (`ai_rate_limits_by_level` как `dict_per_level` — главный
  AI-paywall; `autorole_ids` как `list_length`) — **без правок в самих фичах**.
- **MODULE-фичи — гейт**: `require_tier` на командах (`/git /steam /digest
  /achievements /secret`) и `tier_allows` на событиях (staykick = Pro, «Альбом» =
  Premium). Карта порогов — `MODULE_MIN_TIER`.

Downgrade реализован не отдельной логикой, а принципом **«гейтим создание нового,
активное доживает срок»**: гейты стоят на создании, уже открытые комнаты/сессии/
очереди доживают сами (grace без отдельного прохода).

Раскаткой правит `ENTITLEMENTS_DEFAULT_TIER`: `free` (дефолт) — enforcement ВКЛ;
`pro` — аварийный рубильник «всем максимум».

## Последствия

- **Плюсы:** одно место enforcement'а вместо 88; фича не знает о тарифах; кламп
  покрывает и нескалярные лимиты; аварийное отключение — один флаг.
- **Минусы / цена:** `resolved()` возвращает raw-значение мимо клампа — тарифные
  поля надо читать через `get`; это осознанный footgun (см. ниже).
- **Риски / что осталось открытым:** запретить тарифные поля через `resolved()`
  архитектурно или тестом-гардом, что все `TIERABLE`-ключи не читаются raw-путём.
