# Повторный технический аудит Poposya + WARDEN

**Дата:** 10 августа 2026  
**Версия:** повторный аудит после внесённых изменений  
**Объекты:** `Poposya-main (2).zip` + `WARDEN-master.zip`

---

# 1. Executive Summary

Обновление получилось **очень хорошим**.

По сравнению с предыдущим аудитом закрыта большая часть наиболее важных замечаний:

- WARDEN больше не использует сырой Docker socket напрямую;
- Docker socket вынесен за `docker-socket-proxy`;
- Docker proxy закреплён на `0.3.0`, а не `latest`;
- добавлены отдельные `READ` / `CONTROL` токены WARDEN;
- control API получил rate limit;
- control actions теперь аудируются;
- `/health/full` Poposya можно закрыть отдельным токеном;
- command bridge получил lease/recovery;
- появились `attempts`, `claimed_at`, `worker_id`;
- entitlement system теперь реально работает, а не является только архитектурным швом;
- Free enforcement включён по умолчанию;
- Premium/Pro module gates подключены;
- downgrade реализован через graceful blocking новых операций;
- добавлены тесты для entitlement, tier clamp и command recovery.

**Это уже заметно более production-ready версия системы.**

---

# 2. Проверка тестов

## WARDEN

В текущем окружении тесты запускаются полностью:

```text
415 passed
```

Время выполнения:

```text
8.99 s
```

Это очень хороший результат.

---

## Poposya

`compileall` проходит:

```text
compile = OK
```

Полный pytest в текущем окружении запустить не удалось из-за отсутствующих runtime dependencies:

```text
ModuleNotFoundError: No module named 'discord'
```

То есть ошибка не указывает на падение теста проекта — тестовый runner остановился на этапе импорта.

В `docs/plans/monetization-prep.md` репозиторий заявляет:

```text
1907 passed / 19 skipped
```

а также:

```text
ruff + mypy clean
frontend tsc + vite build
```

Но эти цифры я **не считаю независимо подтверждёнными этим аудитом**, поскольку зависимости Poposya в предоставленном окружении не были установлены.

---

# 3. Новая оценка

| Область | Poposya | WARDEN |
|---|---:|---:|
| Архитектура | 8.8/10 | 9.2/10 |
| Код/структура | 8.5/10 | 9/10 |
| Тестирование | 8.5/10* | 9.8/10 |
| Безопасность | 8/10 | 7.5/10 |
| Production reliability | 8.5/10 | 9/10 |
| Docker/security isolation | 8.8/10 | 7/10 |
| Монетизация | 9/10 technical | — |
| **Общее** | **~8.6/10** | **~8.5/10** |

\* Ограничение оценки Poposya связано с невозможностью воспроизвести полный pytest suite в текущем окружении.

Главный вывод:

> **Система стала существенно лучше. Теперь основные оставшиеся риски находятся не в базовой архитектуре, а в security hardening, идемпотентности команд и переходе от ручной подписки к реальному billing.**

---

# 4. Что было исправлено после прошлого аудита

## C1. WARDEN Docker socket

### Было

```text
WARDEN
  ↓
/var/run/docker.sock
```

или широкий proxy:

```text
POST=1
CONTAINERS=1
```

### Сейчас

```text
WARDEN
    ↓
docker-socket-proxy
    ↓
Docker socket
```

и proxy находится в отдельной:

```text
internal: true
```

сети.

Это значительно лучше.

---

# 5. Но главный Docker security issue всё ещё существует

В `docker-compose.yml` WARDEN:

```yaml
CONTAINERS: 1
POST: 1
```

Комментарий в самом проекте правильно описывает проблему:

> `POST=1` у tecnativa — не whitelist конкретного `/restart`.

При:

```text
CONTAINERS=1
POST=1
```

потенциально доступны POST operations вроде:

```text
/containers/create
/containers/start
/containers/stop
/containers/kill
/containers/update
/containers/rename
/containers/restart
```

То есть архитектурно:

```text
RCE в WARDEN
    ↓
доступ к socket-proxy
    ↓
Docker POST API
    ↓
потенциально host compromise
```

### Severity

**P0 / Critical hardening**

Не потому, что WARDEN сейчас явно экспонирует Docker наружу.

Наоборот, сеть построена хорошо.

Проблема в том, что:

> **если сам WARDEN будет скомпрометирован, Docker control surface всё ещё слишком широкий.**

### Что делать

Перейти на path-level allowlist.

Разрешить только:

```text
GET /version
GET /containers/...
GET /containers/.../json
GET /containers/.../stats
POST /containers/.../restart
```

и запретить:

```text
POST /containers/create
POST /containers/start
POST /containers/stop
POST /containers/kill
POST /containers/update
POST /containers/rename
POST /containers/.../exec
```

Текущий комментарий проекта уже предлагает подход через `wollomatic/socket-proxy` с regex allowlist.

**Это следующий главный security fix.**

---

# 6. WARDEN token architecture стала намного лучше

Теперь есть:

```text
WARDEN_API_TOKEN
WARDEN_CONTROL_TOKEN
```

Read:

```text
GET /status
```

Control:

```text
POST /pause
POST /resume
```

И тесты явно проверяют:

```text
read token → status OK
read token → pause 403
control token → status OK
control token → pause OK
```

Это правильная least-privilege модель.

### Оценка

**Очень хорошо.**

---

# 7. WARDEN control API теперь имеет rate limit

Добавлено:

```text
10 actions / 60 sec
```

для:

```text
pause
resume
```

и rate limit проверяется **до выполнения side effect**.

Это важно.

Тест подтверждает:

```text
10 → OK
11 → 429
pause() не вызывается
```

Также есть аудит:

```text
action
minutes
client
ok
```

Это хорошее operational hardening.

---

# 8. Оставшийся недостаток rate limiter

Сейчас rate limiter:

```text
in-memory
```

То есть после рестарта WARDEN:

```text
control_hits = {}
```

и лимит начинается заново.

Для обычного API это нормально.

Для control-plane это не идеально, но практически **низкий риск**, потому что:

- endpoint внутренний;
- нужен control token;
- есть аудит;
- действия сами по себе ограничены логикой WARDEN.

Можно оставить как есть.

---

# 9. `/health/full` теперь можно закрыть

Это хороший апдейт.

Poposya:

```text
/health
/ready
```

остаются открытыми.

А:

```text
/health/full
```

может требовать:

```text
X-Health-Token
```

WARDEN получает:

```text
WARDEN_HEALTH_TOKEN
```

а Poposya:

```text
HEALTH_FULL_TOKEN
```

с одинаковым значением.

Это хороший trust boundary.

---

# 10. Но порядок включения health token критичен

Проект правильно документирует:

```text
1. WARDEN получает token
2. WARDEN начинает отправлять token
3. только после этого Poposya начинает требовать token
```

Иначе:

```text
Poposya → 401
WARDEN → score 0
WARDEN → CRITICAL
WARDEN → restart
```

Это потенциальная restart loop.

Документация и RUNBOOK уже учитывают эту проблему.

**Это хороший пример зрелой operational документации.**

---

# 11. Command Bridge — серьёзное улучшение

Это, пожалуй, самое важное исправление Poposya после прошлого аудита.

Теперь есть:

```text
claimed_at
worker_id
attempts
lease timeout
recover_stale()
```

Flow:

```text
PENDING
   ↓
RUNNING + lease
   ↓
DONE / FAILED
```

При crash:

```text
RUNNING
   ↓
lease expired
   ↓
recover_stale()
   ↓
PENDING
```

или:

```text
attempts exhausted
   ↓
FAILED
```

Это закрывает предыдущую проблему:

> `RUNNING` навсегда.

---

# 12. Хорошо сделан worker identity

Используется:

```text
pid + random UUID
```

Например:

```text
1234-a91e8c2f
```

После рестарта worker ID меняется.

Это полезно для диагностики и будущего multi-worker режима.

---

# 13. Но идемпотентность команд всё ещё остаётся проблемой

Lease recovery решает:

> «Что делать, если процесс умер?»

Но не полностью решает:

> «Что делать, если Discord action уже произошёл, а процесс умер до `_finish()`?»

Пример:

```text
command = ban user

claim
 ↓
Discord BAN успешно выполнен
 ↓
process crash
 ↓
RUNNING
 ↓
lease expires
 ↓
retry
 ↓
BAN ещё раз
```

Для ban это относительно безопасно.

Но:

```text
role.create
music.skip
role.add
some external side effect
```

могут быть неидемпотентными.

### Рекомендация

Добавить command execution semantics:

```text
idempotency_key
```

и, где возможно:

```text
reconcile_before_execute()
```

Например:

```text
ban user
 ↓
user уже banned?
 ├─ yes → DONE
 └─ no  → execute
```

Это следующий важный reliability layer.

---

# 14. Max attempts = 2

Сейчас:

```text
DEFAULT_MAX_ATTEMPTS = 2
```

Это разумно.

Особенно потому, что некоторые Discord actions не являются идеально идемпотентными.

Я бы пока **не увеличивал этот лимит**.

Лучше сначала добавить reconciliation.

---

# 15. Entitlement system теперь действительно production-shaped

Сейчас есть:

```text
guild_entitlements
```

с:

```text
guild_id
tier
expires_at
granted_by
updated_at
```

и:

```text
EntitlementService
```

с:

- cache;
- expiry;
- grant;
- revoke;
- reload;
- Postgres NOTIFY.

Это уже не «заготовка».

Это рабочая entitlement architecture.

---

# 16. Очень хорошее решение — entitlement sync через NOTIFY

Flow:

```text
Web Panel
   ↓
Postgres
   ↓
pg_notify
   ↓
Poposya
   ↓
reload_guild()
```

Поэтому:

```text
оператор выдал Premium
```

не требует:

```text
restart bot
```

Это правильно.

---

# 17. Enforcement Free теперь включён

В `.env.example`:

```text
ENTITLEMENTS_DEFAULT_TIER=free
```

То есть система больше не находится в режиме:

```text
все PRO
```

по умолчанию.

Это важное изменение.

Теперь:

```text
no entitlement
     ↓
FREE
```

а:

```text
entitlement = PREMIUM
     ↓
Premium
```

---

# 18. Tier Clamp сделан очень хорошо

Вместо:

```python
if premium:
```

по всему проекту используется:

```text
TierClampSettingsProvider
```

и:

```text
TIERABLE
```

Это одно из самых сильных архитектурных решений в текущем обновлении.

Free автоматически получает ограничения:

```text
AI
memory
notes
tempvoice
cinema
finds
/send
autorole
```

без переписывания каждого use case.

---

# 19. Главный AI paywall

Особенно хорошо:

```text
ai_rate_limits_by_level
```

клампится через:

```text
dict_per_level
```

Например:

```text
Free:
≤10 AI replies/hour/level
```

а Premium/Pro получают полный configured value.

Это именно тот механизм, который нужен для будущего unit economics.

---

# 20. Но есть один архитектурный footgun

В `TierClampSettingsProvider`:

```text
get()
```

клампится.

Но:

```text
resolved(guild_id)
```

возвращает полную модель без клампа.

Сам проект это честно документирует.

Это означает:

```text
provider.get(...)
    → tariff-aware

provider.resolved(...).field
    → raw configured value
```

Сейчас конкретные критичные места вроде `relationship_notes_max_chars` дополнительно используют значение через provider в runtime, поэтому непосредственного bypass, который я бы назвал P0, не вижу.

Но архитектурно это опасная ловушка для будущего разработчика.

### Рекомендация

Либо:

1. запретить тарифные поля через `resolved()` архитектурно;
2. сделать `resolved_effective(guild_id)`;
3. или добавить тест, который гарантирует, что все `TIERABLE` fields не читаются через raw resolved path.

---

# 21. Premium module gates

Сейчас:

```text
/git       → Premium
/steam     → Premium
/digest    → Premium
/achievements → Premium
/secret    → Premium
Album      → Premium
staykick   → Pro
```

Это соответствует продуктовой модели.

Особенно хорошо, что событийные фичи используют:

```text
tier_allows()
```

а command-based:

```text
require_tier()
```

То есть учтены оба типа execution path.

---

# 22. Downgrade/grace сделан правильно

Очень хорошее решение:

> не отбирать уже созданное, а запретить создание нового.

Например:

```text
Premium expires
       ↓
existing secret room
       ↓
доживает срок

new secret room
       ↓
DENY
```

Аналогично:

```text
tempvoice
music queue
staykick
```

Это намного лучше, чем массово уничтожать состояние после downgrade.

---

# 23. Но trial сейчас можно использовать повторно

Это **самая заметная бизнес-логическая проблема обновлённой монетизации**.

В `Subscription.tsx`:

```text
if (!sub?.active)
    → показать "Триал 14 дней"
```

И API просто выдаёт:

```text
Premium 14 days
```

Нет:

```text
trial_used_at
trial_eligible
trial_started_at
```

Поэтому возможен сценарий:

```text
Trial 14 days
    ↓
expired
    ↓
Trial 14 days
    ↓
expired
    ↓
Trial 14 days
```

или:

```text
trial
↓
revoke
↓
trial
```

### Severity

**P1 / High для коммерческого запуска**

### Исправление

Добавить отдельное состояние:

```text
trial_started_at
trial_ended_at
trial_used_at
```

или лучше отдельную таблицу/events:

```text
guild_trial
```

с уникальным:

```text
guild_id
```

и серверным enforcement:

```text
trial_used == true
→ trial endpoint forbidden
```

Не доверять UI.

---

# 24. `/premium` улучшился, но ещё не стал полноценным conversion funnel

Сейчас `/premium` хорошо объясняет:

```text
Free
Premium
Pro
```

и делает это голосом Попоси.

Это удачно.

Но всё ещё нет полноценного:

```text
[Попробовать Premium]
[Купить Premium]
[Сравнить]
```

потому что реальной payment layer ещё нет.

Это нормально для текущей стадии.

---

# 25. Самая большая незакрытая часть монетизации — billing

Сейчас:

```text
operator
   ↓
panel
   ↓
PUT entitlement
```

Это административная модель.

Для реального продукта нужно:

```text
Payment Provider
       ↓
Webhook
       ↓
Billing Service
       ↓
EntitlementService
       ↓
guild_entitlements
```

Это правильно оставить именно так.

Не надо давать payment provider прямой контроль над Discord features.

---

# 26. Entitlement API хорошо защищён

API:

```text
GET
PUT
DELETE
```

требует:

```text
require_operator
```

И операции пишутся в audit:

```text
entitlement.grant
entitlement.revoke
```

Это хороший уровень контроля.

---

# 27. Стоит добавить transaction/event id для billing

Когда появится платёжка, webhook может прийти:

```text
1 раз
```

или:

```text
2–5 раз
```

или:

```text
после timeout
```

Поэтому billing должен иметь:

```text
provider_event_id
```

с unique constraint.

Flow:

```text
webhook
 ↓
event_id already processed?
 ├── yes → return 200
 └── no
      ↓
record event
      ↓
grant entitlement
```

Это обязательно для production billing.

---

# 28. Нужен reconciliation job

Даже при webhook архитектуре стоит иметь периодический:

```text
billing reconciliation
```

Например:

```text
каждые 6–24 часа
```

Проверяет:

```text
local entitlement
vs
payment provider subscription
```

Это защищает от:

- потерянного webhook;
- ручного chargeback;
- отмены подписки;
- ошибок интеграции.

---

# 29. Нужна политика grace period для оплаты

Не стоит делать:

```text
payment missed
↓
FREE instantly
```

Лучше:

```text
payment failed
↓
grace 1–3 days
↓
retry
↓
still failed
↓
downgrade
```

Это особенно важно для recurring subscriptions.

---

# 30. WARDEN persistence стала заметно лучше

SQLite теперь:

```text
WAL
synchronous=NORMAL
busy_timeout=5000
```

Это хороший апдейт.

Также появился retention tiering:

```text
0–48h
    full resolution

48h–14d
    1 sample/hour

14–90d
    1 sample/day

>90d
    delete
```

Это значительно лучше бесконечного роста `samples`.

---

# 31. Но SQLite всё ещё синхронный

WARDEN продолжает использовать:

```python
sqlite3
```

в async application.

При текущих:

```text
4 targets
10 sec interval
```

это нормально.

Но при росте:

```text
20–50 targets
```

может появиться event-loop blocking.

### Severity

**P2**

Не нужно срочно переписывать.

Если WARDEN останется маленьким single-process watchdog:

> SQLite вполне нормален.

Если он станет универсальным monitoring service:

> переходить на `aiosqlite`, worker thread или PostgreSQL.

---

# 32. WARDEN sample storage сейчас выглядит хорошо

После tiering:

```text
raw hot data
↓
hourly warm
↓
daily cold
↓
delete
```

Это уже production-like retention policy.

---

# 33. Restart budget остаётся сильной стороной

Сейчас:

```text
≤1 restart / episode
≤3 / hour
```

и budget переживает restart самого WARDEN благодаря SQLite.

Это очень хорошее решение.

Именно здесь WARDEN заметно лучше обычного:

```text
if unhealthy:
    docker restart
```

watchdog.

---

# 34. Decision Engine остаётся одной из лучших частей проекта

Архитектура:

```text
HEALTH
 ↓
SCORE
 ↓
DIAGNOSIS
 ↓
DECISION
 ↓
ACTION
```

и decision engine остаётся чистой функцией.

Это делает систему:

- тестируемой;
- объяснимой;
- предсказуемой;
- менее склонной к restart loops.

---

# 35. Dependency/victim model

Также очень хорошая часть.

WARDEN различает:

```text
root cause
```

и:

```text
victim
```

Например:

```text
DB DOWN
 ↓
API unhealthy
 ↓
WEB unhealthy
```

Не нужно рестартовать всё подряд.

Это правильный monitoring model.

---

# 36. Сам watchdog WARDEN

Механизм:

```text
last_tick
 ↓
event loop stalled
 ↓
os._exit(1)
 ↓
Docker restart
```

остаётся хорошим.

То есть:

> watchdog контролирует собственную живость.

---

# 37. Что я бы ещё добавил WARDEN

После restart сейчас стоит усилить:

```text
restart
 ↓
grace
 ↓
health verification
 ↓
recovered?
```

И хранить:

```text
restart_started_at
restart_finished_at
recovery_time
post_restart_score
```

Тогда можно измерять:

```text
MTTR
```

и понимать:

> WARDEN действительно лечит систему или просто перезапускает её.

---

# 38. Ещё один полезный показатель

Добавить:

```text
restart_effectiveness
```

Например:

```text
restart → recovered
restart → not recovered
```

и:

```text
recovery_rate = recovered / restarts
```

Если:

```text
30 restarts
5 recoveries
25 failures
```

это уже сигнал:

> проблема не в process crash, а в инфраструктуре / зависимости.

---

# 39. Poposya Docker security

Здесь всё выглядит хорошо.

Есть:

```text
USER app
UID 10001
cap_drop: ALL
no-new-privileges
mem_limit
```

Для bot/api.

Это хороший defence-in-depth.

---

# 40. Web container

Сейчас:

```text
cap_drop ALL
+
только необходимые capabilities
```

Это нормально.

Rootless nginx остаётся optional hardening.

Я бы пока не ставил его выше других задач.

---

# 41. PostgreSQL

Сейчас:

```text
Postgres 16 Alpine
```

с:

```text
mem_limit 2g
healthcheck
persistent volume
```

Это нормально.

Следующий уровень:

- backup restore test;
- WAL/PITR при необходимости;
- monitoring disk usage;
- connection pool limits.

---

# 42. Backup

Backup subsystem выглядит хорошо.

Но ключевое правило остаётся:

> backup не считается надёжным, пока не сделан restore test.

Я бы сделал периодический тест:

```text
pg_dump
 ↓
temporary postgres
 ↓
restore
 ↓
migration/schema verification
 ↓
DROP
```

Хотя бы вручную раз в месяц.

---

# 43. Health API

Разделение:

```text
/health
/ready
/health/full
```

хорошее.

Особенно теперь:

```text
/health/full
```

можно закрыть token'ом.

---

# 44. Session security Web Panel

В проекте есть:

```text
WEB_SESSION_VERSION
WEB_SESSION_TTL_HOURS
WEB_IDLE_TTL_MINUTES
HttpOnly
SameSite=Lax
```

Это хороший набор.

Но ранее отмеченная проблема с динамическими Discord permissions всё ещё актуальна концептуально:

```text
OAuth permission
↓
JWT/session
↓
server access
```

Если permission claims кэшируются слишком долго, пользователь может сохранить права после изменения роли в Discord.

### Рекомендация

Сделать:

```text
identity session
+
short permission cache
```

а не 24h immutable authorization state.

---

# 45. CSRF

Cookie-based session + state-changing API всё ещё стоит усилить CSRF protection.

Особенно когда панель будет публичной.

Сейчас:

```text
SameSite=Lax
```

уже даёт хорошую защиту от части сценариев.

Но полноценный:

```text
CSRF token
```

будет лучше.

### Priority

**P2**

---

# 46. Security hardening: token comparison

В WARDEN токены проверяются через обычное:

```python
got in allowed
```

Для внутреннего Docker API это практически не проблема.

Но для sensitive control-plane можно использовать:

```text
hmac.compare_digest()
```

если нужен defence-in-depth против timing analysis.

### Priority

**P3**

---

# 47. Текущая архитектура в целом

Сейчас система выглядит примерно так:

```text
                         Discord
                            │
                            ▼
                    ┌──────────────┐
                    │   POPOSYA    │
                    │              │
                    │ Bot          │
                    │ API          │
                    │ Web          │
                    │ PostgreSQL   │
                    └──────┬───────┘
                           │
                   health / commands
                           │
                           ▼
                    ┌──────────────┐
                    │    WARDEN    │
                    │              │
                    │ probe        │
                    │ score        │
                    │ diagnose     │
                    │ decision     │
                    │ restart      │
                    └──────┬───────┘
                           │
                           ▼
                 restricted Docker API
                           │
                           ▼
                      Docker Host
```

Это хорошая архитектура.

---

# 48. Что я бы НЕ делал

Не надо сейчас:

- переписывать Poposya на микросервисы;
- выносить AI в отдельный сервис;
- менять SQLite WARDEN только ради «правильности»;
- делать Kubernetes;
- добавлять сложный event bus между всеми компонентами;
- переписывать entitlement system.

Фундамент уже хороший.

---

# 49. Приоритетный roadmap

## P0

### 1. Ограничить Docker proxy path-level allowlist

Главный оставшийся security issue.

---

## P1

### 2. Защитить trial от повторного использования

```text
trial_used
```

должен проверяться на backend.

### 3. Сделать Discord command execution идемпотентным

Особенно:

```text
ban
unban
role.*
external side effects
```

### 4. Добавить billing event idempotency

Когда появится payment provider:

```text
provider_event_id UNIQUE
```

---

## P2

### 5. Permission revalidation

Уменьшить доверие к 24h permission snapshot.

### 6. CSRF

### 7. Backup restore test

### 8. Restart verification metrics

---

## P3

### 9. `hmac.compare_digest`

### 10. Rootless nginx

### 11. Read-only rootfs

### 12. Async/threaded WARDEN SQLite при росте нагрузки

---

# 50. Финальная оценка

## Poposya

**~8.6/10**

Проект уже выглядит как реальный production application, а не Discord pet-project.

Особенно сильны:

- modular architecture;
- Postgres;
- durable command bridge;
- entitlement system;
- centralized tier enforcement;
- Web Panel;
- health/readiness;
- Docker hardening;
- extensive testing;
- graceful downgrade.

---

## WARDEN

**~8.5/10**

Особенно сильны:

- Decision Engine;
- hysteresis/debounce;
- dependency graph;
- victim suppression;
- restart budget;
- persistent state;
- self-watchdog;
- 415 проходящих тестов;
- Docker socket isolation;
- read/control token split;
- rate limiting;
- audit log.

Главный минус:

> Docker proxy всё ещё предоставляет слишком широкий POST surface.

---

# 51. Главное изменение по сравнению с прошлым аудитом

Если раньше я бы сказал:

> «Архитектура хорошая, но есть несколько опасных production holes.»

то сейчас я бы сказал:

> **«Архитектура уже production-oriented; теперь нужно закрывать конкретные security/reliability edge cases и подключать коммерческий billing.»**

Это заметный прогресс.

---

# 52. Финальный список

Если делать только **пять вещей**, я бы сделал именно их:

```text
1. Docker path-level allowlist
        ↓
2. Trial one-time enforcement
        ↓
3. Command idempotency/reconciliation
        ↓
4. Billing webhook idempotency
        ↓
5. Discord permission revalidation
```

После этого система будет уже очень близка к тому уровню, который я бы спокойно рассматривал как **серьёзный production SaaS/Discord platform**, а не просто хорошо сделанный бот.

---

# 53. Вердикт

**Обновление однозначно удачное.**

Особенно приятно видеть, что предыдущие рекомендации были реализованы не точечно, а через архитектурные швы:

```text
entitlement
     ↓
tier provider
     ↓
clamp / gate
     ↓
feature
```

и:

```text
command
   ↓
lease
   ↓
attempts
   ↓
recovery
```

и:

```text
WARDEN
   ↓
read/control separation
   ↓
rate limit
   ↓
audit
```

То есть ты не просто «починил баги», а **укрепил архитектуру в правильных местах**.

Следующий этап для Poposya — уже не столько кодирование новых фич, сколько:

**security hardening → billing → observability → unit economics → production launch.**
