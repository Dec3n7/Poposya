# Проектные документы: ТЗ, планы, спеки, идеи

Здесь собраны техническое задание, планы реализации, архитектурные спеки, аудиты
и банк идей. Это запись о том, **как делали** и **что хотели** — исторический
контекст и аргументация.

- **Архитектурные решения** («что решили и почему») вынесены в
  [../adr/](../adr/README.md). Каждый план ниже линкуется на ADR, где его решение
  зафиксировано; ADR ссылается обратно на план как на первоисточник.
- Актуальный состав фич и запуск — в корневом [README](../../README.md); правила
  слоёв — в [ARCHITECTURE.md](../ARCHITECTURE.md); эксплуатация — в
  [RUNBOOK.md](../RUNBOOK.md).

Статус у каждого документа проставлен в его шапке. Реализованные планы не удаляем.

## Планы и спеки

| Документ | Что это | Статус | Решение → ADR |
|---|---|---|---|
| [TZ.md](TZ.md) | Базовое ТЗ (модульная гексагональная архитектура) | ✅ Реализовано | [ADR-007](../adr/ADR-007-hexagonal-dependency-rule.md) |
| [WEB_PANEL_ARCHITECTURE.md](WEB_PANEL_ARCHITECTURE.md) | Архитектурная спека веб-панели | ✅ Референс | [ADR-012](../adr/ADR-012-web-panel-reuses-domain.md) |
| [WEB_PANEL_PLAN.md](WEB_PANEL_PLAN.md) | План реализации веб-панели | ✅ Реализовано | [ADR-012](../adr/ADR-012-web-panel-reuses-domain.md) |
| [CONCURRENCY_PLAN.md](CONCURRENCY_PLAN.md) | Закалка конкурентности под панель (Postgres, мост) | ✅ Реализовано | [ADR-001](../adr/ADR-001-postgresql-production-database.md), [ADR-010](../adr/ADR-010-panel-bot-listen-notify.md) |
| [guild_settings_plan.md](guild_settings_plan.md) | Пер-серверные настройки (`/config` + панель) | ✅ Реализовано | [ADR-008](../adr/ADR-008-per-guild-settings-provider.md) |
| [monetization-prep.md](monetization-prep.md) | Тарифы / entitlement: подготовка и реализация | ✅ Реализовано | [ADR-009](../adr/ADR-009-tier-enforcement-seam.md), [ADR-014](../adr/ADR-014-entitlement-grant-and-billing.md) |
| [premium-keys.md](premium-keys.md) | Активация Premium/Pro по самоподписанным ключам | 🟢 Частично в коде | [ADR-014](../adr/ADR-014-entitlement-grant-and-billing.md) |
| [hunt_sys.md](hunt_sys.md) | Концепт «Ночные находки Попоси» | ✅ Реализовано | — |
| [music TZ.md](music%20TZ.md) | UX-бэклог музыкального плеера | ✅ Реализовано | — |
| [persona_library_plan.md](persona_library_plan.md) | Библиотеки персон: 1 персонаж = 1 библиотека | ✅ Реализовано | — |
| [Poposya_ideas.md](Poposya_ideas.md) | Банк идей «живой персоны» | ✅ Реализовано | — |
| [Poposya_ideas2.md](Poposya_ideas2.md) | Идеи участия персоны в жизни сервера | ✅ Реализовано | — |
| [achievements.md](achievements.md) | Ачивки сервера с рендер-карточками | 🟡 Черновик | — |

## Идеи и оценки

| Документ | Что это |
|---|---|
| [MONETIZATION_IDEAS.md](MONETIZATION_IDEAS.md) | Продуктовые идеи и прайс монетизации (черновик) |
| [Poposya_improvement_plan.md](Poposya_improvement_plan.md) | План улучшения и продуктовая оценка (источник [ADR-005](../adr/ADR-005-requirements-lock-source-of-truth.md), [ADR-006](../adr/ADR-006-feature-oriented-architecture.md)) |
| [Poposya_WARDEN_Technical_Audit_v2.md](Poposya_WARDEN_Technical_Audit_v2.md) | Повторный техаудит (источник [ADR-011](../adr/ADR-011-durable-command-bridge.md), [ADR-013](../adr/ADR-013-warden-docker-socket-allowlist.md)) |
| [perf-baseline.md](perf-baseline.md) | Замер производительности под нагрузкой + оценка ёмкости и VPS (2026-08-16) |
| [scale-300-guilds.md](scale-300-guilds.md) | План масштабирования до 300 активных гильдий: рычаги, порядок, риск-матрица (2026-08-16) |
| [WARDEN.md](WARDEN.md), [WARDEN V2.md](WARDEN%20V2.md) | Ранние design-спеки WARDEN (актуальная — в [WARDEN/DESIGN.md](../../../WARDEN/DESIGN.md); решение — [ADR-004](../adr/ADR-004-warden-independent.md)) |

## Вне кода

- **Хостинг** — вынести бота на постоянный сервер.
- **Настоящий TLS** — заменить самоподписанный сертификат панели на выданный CA
  (см. [web/TLS.md](../../web/TLS.md)).
- **Ачивки** — [achievements.md](achievements.md), каталог обсуждается.

> Контент-ассеты (системный промпт персоны, текст правил сервера) переехали из
> `docs/` в [../content/](../content/).
