# ADR-012 — Веб-панель — второй клиент того же домена через API

- **Статус:** Accepted (2026-07-18)
- **Связано:** [ADR-007](ADR-007-hexagonal-dependency-rule.md), [ADR-010](ADR-010-panel-bot-listen-notify.md), [ADR-011](ADR-011-durable-command-bridge.md), план-первоисточник — [WEB_PANEL_ARCHITECTURE](../plans/WEB_PANEL_ARCHITECTURE.md), [WEB_PANEL_PLAN](../plans/WEB_PANEL_PLAN.md)

## Контекст

Нужна админ-панель по серверам. Соблазн — сделать её отдельным проектом со своей
копией бизнес-правил. Это привело бы к двум расходящимся реализациям одной логики
и вечной рассинхронизации «бот умеет так, а панель иначе».

## Решение

**Веб-панель — не отдельный проект, а второй клиент той же бизнес-логики.**
Discord-бот и Web ничего не знают друг о друге и вызывают **одни и те же use
case'ы** ([ADR-007](ADR-007-hexagonal-dependency-rule.md)):

```text
Discord ─┐                        ┌─ React Web Panel
         ├→ Application → Domain ←┤
   (cogs) ┘   (общие UseCase)     └─ REST API (FastAPI, OAuth, сессии, аудит)
```

- Реализация — `src/api` (FastAPI: OAuth, сессии, аудит) + фронт `web/`
  (React + TS + Vite); бизнес-код не дублируется.
- Панель пишет в ту же БД ([ADR-001](ADR-001-postgresql-production-database.md));
  кэш бота инвалидируется через `LISTEN/NOTIFY` ([ADR-010](ADR-010-panel-bot-listen-notify.md)),
  а Discord-действия исполняются через командный мост
  ([ADR-011](ADR-011-durable-command-bridge.md)).

## Последствия

- **Плюсы:** одна реализация правил на все интерфейсы; новый клиент = новый
  presentation поверх тех же use case'ов; долговечность без дублирования.
- **Минусы / цена:** двухпроцессная запись потребовала закалки конкуренции
  ([ADR-001](ADR-001-postgresql-production-database.md), CONCURRENCY_PLAN) и моста
  для действий, требующих gateway бота.
- **Риски / что осталось открытым:** безопасность сессий и ревалидация Discord-
  прав (не доверять 24-часовому снимку прав), CSRF-токен для публичной панели —
  см. Technical_Audit_v2 §44–45.
