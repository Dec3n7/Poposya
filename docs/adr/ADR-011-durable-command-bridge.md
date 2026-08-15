# ADR-011 — Долговечный командный мост: lease, recovery, идемпотентность

- **Статус:** Accepted (2026-08)
- **Связано:** [ADR-010](ADR-010-panel-bot-listen-notify.md), [ADR-012](ADR-012-web-panel-reuses-domain.md), первоисточники — [Technical_Audit_v2 §11–14](../plans/Poposya_WARDEN_Technical_Audit_v2.md), код — `src/infrastructure/commands/`, `src/infrastructure/discord/command_executor.py`

## Контекст

Панель инициирует действия, которые исполняет **бот** (у него есть Discord-gateway
и голос): бан, выдача роли, управление плеером. Команда переживает процессы: её
записывает API, исполняет бот. Наивная модель «PENDING → RUNNING → DONE» ломается
на краше: воркер, умерший в `RUNNING`, оставляет команду навсегда «выполняющейся»,
а слепой ретрай может повторить уже случившийся побочный эффект (двойной бан).

## Решение

**Командный мост с lease и восстановлением**, поверх транспорта
[ADR-010](ADR-010-panel-bot-listen-notify.md):

- У записи есть `claimed_at`, `worker_id` (`pid + UUID`), `attempts`, lease-таймаут.
  Поток: `PENDING → RUNNING+lease → DONE/FAILED`. При крахе истёкший lease
  переводит запись обратно в `PENDING` (`recover_stale()`), исчерпание `attempts`
  (`DEFAULT_MAX_ATTEMPTS = 2`) → `FAILED`.
- **Идемпотентность через reconciliation перед действием.** Идемпотентные команды
  сверяют состояние до эффекта: ban/tempban — `_already_banned` («уже в бане» →
  DONE без повторного бана), role.assign/unassign — наличие роли, pause/resume —
  `is_paused`, import/preset — существующее имя.
- **Неидемпотентные команды помечены явно** (`NON_IDEMPOTENT_COMMANDS`:
  `role.create`, `music.skip/previous/shuffle/remove`) — при протухшем lease мост
  помечает их `FAILED`, а не ретраит (повтор снял бы другой трек / создал дубль).

## Последствия

- **Плюсы:** нет «вечного RUNNING»; краш между действием и `_finish()` не
  дублирует эффект у идемпотентных команд; `worker_id` даёт диагностику и задел
  под multi-worker.
- **Минусы / цена:** каждая новая команда требует классификации
  идемпотентная/нет и, для идемпотентной, реального reconcile-запроса.
- **Риски / что осталось открытым:** пока `attempts=2` разумно **не** повышать —
  сперва расширять reconciliation; будущий billing-webhook потребует своей
  идемпотентности ([ADR-014](ADR-014-entitlement-grant-and-billing.md)).
