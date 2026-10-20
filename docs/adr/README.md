# Architecture Decision Records (ADR)

Здесь фиксируются **архитектурные решения** проекта: что решили, в каком
контексте и с какими последствиями. ADR отвечает на вопрос «почему так, а не
иначе» — и переживает того, кто решение принял.

- **Решения** живут здесь (`docs/adr/`).
- **Планы реализации, ТЗ и банк идей** — в [../plans/](../plans/README.md): это
  запись о том, *как делали* и *что хотели*, а не о том, *что решили*. ADR
  ссылается на план-первоисточник, план — обратно на ADR.
- **Живые правила слоёв** — в [../ARCHITECTURE.md](../ARCHITECTURE.md) и
  [../AI_RULES.md](../AI_RULES.md). ADR-007 фиксирует само это решение; сами
  правила остаются отдельным живым справочником.
- **Эксплуатация** — в [../RUNBOOK.md](../RUNBOOK.md).

## Что такое ADR и когда его заводить

Заводи ADR, когда решение:

- задаёт или меняет границу между подсистемами;
- выбирает технологию/подход, отказ от которого будет дорогим;
- вводит правило, которому обязан следовать весь новый код;
- сознательно принимает риск или откладывает работу.

Не заводи ADR на мелкие тактические правки, разовые баг-фиксы и то, что и так
однозначно следует из уже принятого решения.

## Статусы

| Статус | Значение |
|---|---|
| `Proposed` | предложено, обсуждается, ещё не действует |
| `Accepted` | принято и действует |
| `Superseded by ADR-NNN` | заменено более новым решением |
| `Deprecated` | больше не актуально, но не заменено |

Принятый ADR **не редактируют по смыслу** — если решение изменилось, заводят
новый ADR и помечают старый `Superseded`. Править можно опечатки и ссылки.

## Шаблон

```markdown
# ADR-NNN — Короткий заголовок в форме решения

- **Статус:** Accepted (ГГГГ-ММ-ДД)
- **Связано:** ADR-XXX, план-первоисточник

## Контекст

Что за силы действуют, какую проблему решаем, какие ограничения.

## Решение

Что именно решили. Одно решение на ADR, в настоящем времени.

## Последствия

- **Плюсы:** что стало лучше.
- **Минусы / цена:** чем платим.
- **Риски / что осталось открытым:** за чем следить.
```

## Реестр

| ADR | Решение | Статус |
|---|---|---|
| [001](ADR-001-postgresql-production-database.md) | PostgreSQL — производственная БД; SQLite только для локалки и тестов | Accepted |
| [002](ADR-002-renderer-isolated-service.md) | Renderer вынесен в изолированный сервис | Accepted |
| [003](ADR-003-event-bus-outbox.md) | Критичные доменные события — через event bus + transactional outbox | Accepted |
| [004](ADR-004-warden-independent.md) | WARDEN — независимый деплой, не часть приложения | Accepted |
| [005](ADR-005-requirements-lock-source-of-truth.md) | `requirements.lock` — источник истины для деплоя | Accepted |
| [006](ADR-006-feature-oriented-architecture.md) | Поэтапная миграция на feature-oriented структуру | Accepted |
| [007](ADR-007-hexagonal-dependency-rule.md) | Гексагональная модульная архитектура + Dependency Rule | Accepted |
| [008](ADR-008-per-guild-settings-provider.md) | Пер-серверные настройки через единый settings-provider | Accepted |
| [009](ADR-009-tier-enforcement-seam.md) | Enforcement тарифов через единый шов clamp/gate | Accepted |
| [010](ADR-010-panel-bot-listen-notify.md) | Интеграция панель↔бот через Postgres `LISTEN/NOTIFY` | Accepted |
| [011](ADR-011-durable-command-bridge.md) | Долговечный командный мост: lease, recovery, идемпотентность | Accepted |
| [012](ADR-012-web-panel-reuses-domain.md) | Веб-панель — второй клиент того же домена через API | Accepted |
| [013](ADR-013-warden-docker-socket-allowlist.md) | Доступ WARDEN к Docker — через фильтрующий socket-proxy | Accepted |
| [014](ADR-014-entitlement-grant-and-billing.md) | Ручная/ключевая выдача тарифа; платёжка — идемпотентным webhook позже | Accepted |
| [015](ADR-015-news-sources-valorant.md) | Новости игр Riot (Valorant/LoL/TFT/Wild Rift/2XKO) — независимая вертикаль `/riot` через `__NEXT_DATA__`; steam не трогаем | Accepted |
| [016](ADR-016-unified-news-poller.md) | Все новостные трекеры — через единый `NewsPoller`; локальные циклы в когах удаляются | Accepted |
| [017](ADR-017-broad-exception-policy.md) | Broad exception — только на operational-границе с recovery и логированием | Accepted |
| [018](ADR-018-health-schema-versioning.md) | Health-схема бот↔WARDEN версионируется (`health_schema_version`) и закреплена contract-тестами | Accepted |
| [019](ADR-019-hidden-achievements-and-hooks.md) | Скрытые ачивки — класс с полем `hidden`; вторая волна — аддитивные события шины | Accepted |
| [020](ADR-020-dm-home-guild.md) | DM-фичи отношений — через явно выбранную «домашнюю» гильдию профиля | Accepted |
| [021](ADR-021-render-static-dynamic-card-split.md) | Карточки — статичная подложка (ассеты) + дешёвый composite динамики (A6) | Accepted |
