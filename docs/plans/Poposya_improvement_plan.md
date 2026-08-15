# Poposya — план улучшения и продуктовая оценка

Дата: 12 августа 2026

## 1. Главная цель

Не пытаться уменьшить Poposya по количеству строк.

Цель рефакторинга:

> Уменьшить локальную когнитивную сложность, количество связей между подсистемами и количество причин для изменения одного файла.

Poposya уже является большим modular monolith. Его не нужно превращать в маленький проект и не нужно сейчас дробить на микросервисы.

Правильное направление:

- оставить modular monolith;
- локализовать features;
- сделать application/use-case слой тоньше;
- сделать Discord/API presentation тонким;
- уменьшить God Objects;
- сохранить сильные reliability/security решения;
- формализовать границы Poposya ↔ WARDEN.

---

## 2. Целевое состояние

Ориентировочная структура:

```text
src/
├── features/
│   ├── persona/
│   │   ├── domain/
│   │   ├── application/
│   │   ├── infrastructure/
│   │   └── presentation/
│   ├── moderation/
│   ├── music/
│   ├── relationship/
│   ├── cinema/
│   ├── achievements/
│   └── ...
│
├── infrastructure/
│   ├── database/
│   ├── discord/
│   ├── ai/
│   ├── http/
│   ├── renderer/
│   └── observability/
│
├── shared/
└── bootstrap/
    └── container.py
```

Это не требует одномоментного переноса всего проекта. Структуру следует вводить feature-by-feature.

---

# 3. Что оставить как есть

Не делать рефакторинг ради самого рефакторинга.

Сохранить:

- modular/hexagonal architecture;
- Domain/Application/Infrastructure boundaries;
- UnitOfWork;
- repositories;
- outbox;
- event bus;
- PostgreSQL в production;
- PostgreSQL integration tests;
- SQLite для быстрого локального запуска/тестов;
- SSRF protection;
- non-root containers;
- `cap_drop`;
- `no-new-privileges`;
- dependency hash locking;
- отдельный renderer;
- WARDEN как отдельный deployment;
- Docker socket proxy;
- health scoring;
- dependency cascade suppression;
- restart budget;
- explainable restart decisions.

---

# 4. P0 — привести зависимости к единому источнику истины

## Сейчас

В проекте одновременно присутствуют:

- `pyproject.toml`;
- `requirements.txt`;
- `requirements.lock`.

Часть metadata/dependency information выглядит исторически рассинхронизированной с production lock.

## Лучше

Выбрать явную модель:

```text
pyproject.toml
    = metadata + tooling

requirements.lock
    = production/deployment source of truth
```

или выбрать другой единый механизм.

Добавить автоматическую проверку, если два файла должны оставаться синхронизированными.

## Почему

Новый разработчик должен за несколько минут понять:

> Где определены реальные production dependencies?

---

# 5. P1 — PersonaRegistry (~4700 строк)

Это главный кандидат на рефакторинг.

## Сейчас

Один большой registry концентрирует:

- загрузку;
- валидацию;
- разрешение persona;
- inheritance;
- presets;
- templates;
- defaults;
- aliases;
- overrides;
- persistence;
- orchestration.

## Лучше

Разделить по ответственности:

```text
persona/
├── domain/
│   ├── model.py
│   ├── inheritance.py
│   ├── validation.py
│   └── rules.py
│
├── application/
│   ├── resolve.py
│   ├── create.py
│   ├── update.py
│   ├── list.py
│   └── registry.py
│
├── infrastructure/
│   ├── repository.py
│   ├── loader.py
│   └── persistence.py
│
└── presentation/
```

Не создавать новый `PersonaService` на несколько тысяч строк.

Use case должен быть маленьким:

```text
ResolvePersona
UpdatePersona
ValidatePersona
LoadPersona
SavePersona
ResolveInheritance
```

## Почему

Цель не в уменьшении LOC. Цель — чтобы изменение одной части Persona не требовало понимать остальные.

---

# 6. P1 — уменьшить Cogs

Особенно большие Cogs вроде Music и Moderation не нужно просто разбивать на `cog1.py`, `cog2.py`.

## Сейчас

Один Cog местами одновременно:

```text
Discord interaction
→ validation
→ permissions
→ business logic
→ repository
→ external API
→ event
→ formatting
```

## Лучше

```text
Discord Cog
    ↓
UseCase
    ↓
Domain
    ↓
Repository / Port
    ↓
Infrastructure
```

Например:

```text
music/
├── domain/
├── application/
│   ├── play.py
│   ├── pause.py
│   ├── skip.py
│   ├── enqueue.py
│   └── leave.py
├── infrastructure/
│   ├── youtube.py
│   ├── ffmpeg.py
│   └── voice.py
└── presentation/
    └── cog.py
```

Cog должен заниматься почти исключительно Discord-specific concerns.

---

# 7. P1 — уменьшить RootContainer

## Сейчас

RootContainer знает о слишком большом количестве feature dependencies.

## Лучше

```text
RootContainer
├── InfrastructureContainer
├── PersonaContainer
├── ModerationContainer
├── MusicContainer
├── RelationshipContainer
└── CinemaContainer
```

RootContainer становится composition root.

Feature container отвечает только за создание своих зависимостей.

## Почему

Это уменьшает coupling и делает feature практически автономной.

---

# 8. P2 — постепенно перейти к feature-oriented структуре

Не переносить весь проект сразу.

Порядок:

1. Persona.
2. Moderation.
3. Music.
4. Relationship.
5. Cinema.
6. Остальные feature.

После каждого переноса:

- тесты;
- импорт-check;
- mypy;
- lint;
- PostgreSQL tests;
- CI.

## Критерий успеха

Изменение одной feature должно требовать открытия минимального количества файлов вне её директории.

---

# 9. Shared — держать маленьким

Не допустить появления нового:

```text
shared/utils.py
shared/helpers.py
shared/services.py
```

на тысячи строк.

В shared должны жить только действительно generic primitives:

- Result;
- Clock;
- IDs;
- pagination primitives;
- common exceptions;
- маленькие generic utilities.

Если utility знает о Persona, Music или Moderation — скорее всего, она принадлежит соответствующей feature.

---

# 10. Event Bus

Сохранить event-driven взаимодействие между независимыми features.

Например:

```text
Moderation
    ↓
UserBanned
    ↓
Achievement
    ↓
Notification
```

Но чётко различать:

### Domain events

```text
UserBanned
PersonaChanged
RelationshipLevelChanged
```

### Application/integration actions

```text
SendDiscordNotification
RebuildCache
UpdateProjection
```

Не превращать event bus в глобальную магическую шину.

---

# 11. Repository layer

Не дробить repository без необходимости.

Плохо:

```text
UserRepository
UserReadRepository
UserWriteRepository
UserQueryRepository
UserStorageService
UserPersistenceService
```

если все они делают почти одно и то же.

Лучше один понятный repository до тех пор, пока разные ответственности реально не требуют разделения.

---

# 12. Правило размера файлов

Не использовать жёсткий lint:

```text
>500 lines = bad
```

Использовать ориентир:

| Размер | Реакция |
|---|---|
| <300 | обычно ничего |
| 300–600 | проверить cohesion |
| 600–1000 | искать естественные группы |
| 1000–1500 | вероятно пора делить |
| 1500–3000 | почти наверняка делить |
| 3000+ | искать несколько подсистем |
| 4700 | рефакторить |

Главный критерий:

> Количество причин для изменения важнее количества строк.

---

# 13. WARDEN не усложнять

WARDEN следует оставить намеренно простым.

Целевая модель:

```text
probe
  ↓
measure
  ↓
score
  ↓
decision
  ↓
action
```

Не добавлять туда бизнес-логику Poposya, сложный event bus, plugin framework или лишние абстракции.

Сила WARDEN — в простоте.

---

# 14. Улучшения WARDEN

### 14.1 Container discovery

Сейчас есть coupling к Compose container names.

Лучше использовать Docker labels:

```text
com.poposya.role=bot
com.poposya.role=api
com.poposya.role=db
```

WARDEN ищет targets по labels.

### 14.2 Restart mode

Вместо только:

```text
dry_run = true/false
```

можно иметь:

```text
disabled
armed
automatic
```

### 14.3 Remediation metrics

Собирать:

- restart reason;
- restart outcome;
- time-to-recovery;
- false-positive rate;
- restart frequency;
- repeated failure rate.

---

# 15. Contract между Poposya и WARDEN

Добавить versioned health contract.

Например:

```text
health_schema_version = 1
```

WARDEN должен иметь contract tests против формата Poposya health endpoint.

Цель:

> изменение health API не должно случайно сломать autonomous remediation.

---

# 16. Broad exception audit

Найти все:

```python
except Exception:
```

Для каждого решить:

1. Это operational boundary?
2. Есть ли стратегия восстановления?
3. Есть ли нормальный logging?
4. Может ли ошибка скрыть programming bug?
5. Нужно ли продолжать execution?

Broad exception допустим на boundary, где есть осознанная recovery strategy.

---

# 17. Что НЕ делать

Не:

- переписывать Poposya с нуля;
- переходить на микросервисы только из-за размера;
- дробить файлы по принципу «500 строк»;
- создавать `*Service` для каждого действия;
- создавать десятки generic repositories;
- складывать feature logic в `shared`;
- превращать event bus в глобальную магию;
- делать WARDEN умнее без необходимости;
- удалять рабочие abstractions только ради уменьшения LOC.

---

# 18. Как измерять успех

Не измерять:

```text
LOC было → LOC стало
```

Измерять:

### Coupling

Сколько внешних модулей требуется для изменения feature?

### Change surface

Сколько файлов нужно открыть для одной задачи?

### Test isolation

Можно ли протестировать feature без запуска всего приложения?

### Composition complexity

Сколько feature-specific dependencies знает RootContainer?

### Failure isolation

Может ли изменение/сбой одной feature затронуть остальные?

### Deployment isolation

Можно ли независимо обновлять renderer/WARDEN?

---

# 19. Рекомендуемый порядок работ

## Этап 1

- dependency source of truth;
- broad exception audit;
- health contract.

## Этап 2

- PersonaRegistry;
- Persona tests;
- Persona container.

## Этап 3

- Music Cog;
- Moderation Cog;
- thin presentation.

## Этап 4

- RootContainer;
- feature containers.

## Этап 5

- feature-oriented directory structure.

## Этап 6

- WARDEN labels;
- restart modes;
- remediation metrics.

---

# 20. Как позиционировать Poposya

Poposya не следует позиционировать как «ещё один Discord bot».

По архитектуре это ближе к:

> Discord community platform / community operating system.

Сильные стороны:

- многофункциональность;
- AI/personas;
- moderation;
- music/voice;
- relationships/community systems;
- web panel;
- API;
- PostgreSQL;
- OAuth;
- event-driven architecture;
- outbox;
- production Docker;
- security hardening;
- automated health/remediation через WARDEN;
- серьёзная тестовая инфраструктура.

Главное конкурентное преимущество — не количество команд само по себе, а то, что несколько подсистем объединены в одну систему.

---

