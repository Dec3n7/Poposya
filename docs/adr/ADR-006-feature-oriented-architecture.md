# ADR-006 — Поэтапная миграция на feature-oriented структуру

- **Статус:** Accepted (2026-08) — направление принято, миграция поэтапная
- **Связано:** [ADR-007](ADR-007-hexagonal-dependency-rule.md), план-первоисточник — [Poposya_improvement_plan §2, §8](../plans/Poposya_improvement_plan.md)

## Контекст

Poposya — большой modular monolith. Он не требует дробления на микросервисы, но
код местами сгруппирован по техническим слоям (`application/`, `infrastructure/`,
`cogs/`), а не по фичам. Из-за этого изменение одной фичи заставляет открывать
файлы во многих директориях: растут coupling и «change surface».

Цель — **не уменьшить число строк**, а снизить локальную когнитивную сложность,
число связей между подсистемами и число причин менять один файл.

## Решение

Целевая структура — **feature-oriented**: каждая фича — самостоятельная вертикаль
`domain / application / infrastructure / presentation`, а `infrastructure/` держит
только по-настоящему общую инфраструктуру (БД, Discord, AI, http, renderer,
observability). `shared/` остаётся маленьким (generic-примитивы), `bootstrap/` —
composition root.

Миграция идёт **feature-by-feature, а не одномоментно**. Порядок: persona →
moderation → music → relationship → cinema → остальные. После каждого переноса —
тесты, import-check, mypy, lint, Postgres-тесты, CI.

Критерий успеха измеряется не в LOC, а в: coupling, change surface, test
isolation, composition complexity, failure/ deployment isolation.

## Последствия

- **Плюсы:** фича становится почти автономной; сбой/изменение одной фичи не
  задевает другие; проще тестировать в изоляции.
- **Минусы / цена:** длительная миграция с сосуществованием старой и новой
  раскладки; риск «недоделанного переезда», если бросить на середине.
- **Риски / что осталось открытым:** не поддаться соблазну дробить файлы по
  правилу «>500 строк» и плодить `*Service`/repository без нужды (см.
  [ADR-007](ADR-007-hexagonal-dependency-rule.md), improvement_plan §11–12, §17).
