# Техническое задание на разработку Discord-бота
## Модульная архитектура с открытостью для расширения

> Статус: базовое ТЗ проекта, в основном реализовано. Актуальный состав фич и
> запуск — в корневом [README](../../README.md); архитектурные правила — в
> [ARCHITECTURE.md](../ARCHITECTURE.md). Этот документ — исходное задание и
> опорный референс для новых модулей.

---

## 1. Цели и требования

- Разработать Discord-бота с архитектурой, рассчитанной на долгий рост числа фич (включая AI-функциональность) без рефакторинга старого кода.
- Обеспечить высокую тестируемость всех слоёв — от бизнес-логики до интеграций — без поднятия реальной БД или Discord-соединения.
- Добиться независимости от внешних фреймворков (Discord API, СУБД, кэш, AI-провайдер) — замена любой из этих компонент не должна затрагивать бизнес-правила.
- Поддерживать переключение между SQLite и PostgreSQL через переменную окружения с минимальными правками, включая корректную работу миграций на обеих СУБД.
- Гарантировать, что фичи не связаны друг с другом напрямую: добавление новой фичи не требует правок кода существующих фич.
- Заложить возможность подключения кэширования (Redis), асинхронных планировщиков и AI-провайдеров без изменения архитектуры.
- Предусмотреть контейнеризацию (Docker) для локальной разработки и продакшн-развёртывания.
- Гарантировать предсказуемое поведение событийной шины при сбоях подписчиков и при росте нагрузки — это отдельная, равная по важности цель, а не деталь реализации.
- Обеспечить наблюдаемость системы — структурированные логи, метрики, трассировка для отладки событийной архитектуры.

**Осознанная асимметрия YAGNI.** Документ намеренно строит инфраструктурные инварианты (Unit of Work, Outbox, Circuit Breaker, метрики, health checks, graceful shutdown) заранее и полно, но требует лениво наращивать бизнес-фичи и общий доменный код (раздел 3: "выносить в shared только после реального дублирования"). Это не противоречие, а разный профиль стоимости изменений: инфраструктурные решения (транзакционная модель, гарантии доставки событий, формат метрик) дорого менять постфактум, когда на них уже завязаны несколько фич — их стоит зафиксировать в начале. Бизнес-логика конкретных фич, наоборот, дешева в изменении и непредсказуема заранее — её преждевременная абстракция создаёт больше вреда, чем экономит. Разработчикам проекта важно понимать это различие, чтобы не переносить принцип "не усложняй заранее" туда, где он не должен применяться, и наоборот.

---

## 2. Общая архитектура (гексагональная + событийная развязка)

Проект строится на четырёх слоях с чёткими границами зависимостей (зависимости идут только внутрь, к домену):

- **Доменный слой (Domain)** — ядро бизнес-логики. Сущности, value-объекты, доменные исключения, бизнес-правила, абстрактные интерфейсы (порты) репозиториев, доменные события и абстракция шины событий. Не зависит ни от каких фреймворков, ORM или Discord. Использует только стандартную библиотеку Python и dataclasses.
- **Слой приложения (Application)** — координация use cases. Pydantic-схемы (DTO) для входа/выхода, сами use cases, мапперы между доменными сущностями и инфраструктурными моделями, интерфейсы внешних адаптеров (Discord-шлюз, кэш, AI-провайдер), обработчики доменных событий (подписчики), DI-фабрики по фичам.
- **Инфраструктурный слой (Infrastructure)** — реализации портов: репозитории (SQLAlchemy ORM), Unit of Work, адаптеры Discord (cogs, event handlers), клиенты AI-провайдеров, кэш (Redis), планировщик задач, реализация шины событий, HTTP-сервер для метрик и health checks.
- **Точка входа (Main)** — собирает все фичевые DI-контейнеры в единый корневой контейнер, настраивает логирование, запускает HTTP-сервер (health/metrics) и бота с корректным graceful shutdown.

### Ключевые принципы

- **Событийная развязка фич** — use case одной фичи никогда не вызывает напрямую use case другой фичи. Вместо этого он публикует доменное событие через `IEventBus`. Другие фичи подписываются на интересующие их события независимо.
- **Unit of Work** — каждый use case работает в рамках транзакции. UOW управляет сессией БД, репозиториями и автоматической публикацией событий при коммите (детали и точный алгоритм — раздел 6.3).
- **Чистая архитектура** — зависимости направлены внутрь, к домену. Инфраструктура зависит от абстракций домена, а не наоборот.

---

## 3. Структура каталогов

```
src/
├── domain/
│   ├── events/
│   │   ├── base.py                # DomainEvent, CriticalDomainEvent — базовые классы событий
│   │   ├── bus.py                 # IEventBus (ABC): publish(), subscribe()
│   │   └── outbox.py              # OutboxItem (сущность для outbox паттерна)
│   ├── economy/                   # пример группировки домена по фиче
│   │   ├── entities/               # User, Transaction
│   │   ├── value_objects/          # Money, Currency, TransactionType
│   │   ├── rules/                  # бизнес-правила (BonusCalculator, FeePolicy)
│   │   ├── repositories/           # ABC-интерфейсы (IUserRepository, ITransactionRepository)
│   │   ├── events/                 # события фичи (CoinsEarned, CoinsSpent, BalanceLow)
│   │   └── exceptions/             # InsufficientFundsError, UserNotFoundError
│   ├── moderation/                # аналогичная структура для другой фичи
│   ├── ai_chat/                   # домен AI-фичи (без привязки к провайдеру)
│   │   ├── entities/               # Conversation, Message
│   │   ├── rules/                  # Policy для фильтрации, PromptTemplate
│   │   └── events/                 # AIMessageGenerated, ToxicContentDetected
│   ├── relationship/              # уровни отношений персоны (раздел 8.5)
│   │   ├── entities/               # RelationshipProfile
│   │   ├── rules/                  # LevelProgressionPolicy, ExclusivityRule
│   │   └── events/                 # RelationshipLevelChanged, ExclusiveGranted
│   └── shared/                    # общие value-объекты (DiscordID, UserID)
│
├── application/
│   ├── economy/
│   │   ├── use_cases/              # AddCoinsUseCase, TransferFundsUseCase
│   │   ├── schemas/                # Pydantic DTO входа/выхода
│   │   ├── mappers/                # ORM ↔ Domain ↔ DTO
│   │   ├── event_handlers/         # подписчики на чужие события
│   │   └── di.py                   # фабрика зависимостей фичи
│   ├── moderation/
│   ├── ai_chat/
│   ├── relationship/              # use cases уровней отношений (раздел 8.5)
│   ├── interfaces/
│   │   ├── discord_gateway.py      # IDiscordGateway
│   │   ├── relationship_reader.py  # IRelationshipReader (читающий порт, раздел 8.5)
│   │   ├── cache_client.py         # ICacheClient
│   │   ├── ai_provider.py          # IAIProvider
│   │   ├── unit_of_work.py         # IUnitOfWork
│   │   ├── feature_flags.py        # IFeatureFlags
│   │   └── rate_limiter.py         # IRateLimiter (per-user троттлинг, раздел 8.4)
│   └── di/
│       ├── root_container.py       # агрегирует фичевые di.py
│       └── bootstrap.py            # инициализация всех компонентов
│
├── infrastructure/
│   ├── db/
│   │   ├── models/                 # SQLAlchemy ORM-модели, сгруппированы по фиче
│   │   ├── repositories/           # реализации репозиториев
│   │   ├── unit_of_work.py         # реализация IUnitOfWork (SQLAlchemy)
│   │   ├── outbox/
│   │   │   ├── model.py            # ORM-модель Outbox
│   │   │   ├── repository.py       # OutboxRepository
│   │   │   └── relay.py            # фоновый процесс публикации
│   │   ├── session.py              # движок, фабрика асинхронных сессий
│   │   └── migrations/             # Alembic (batch mode)
│   ├── discord/
│   │   ├── cogs/                   # слеш-команды, по фиче
│   │   ├── event_handlers/         # on_ready, on_message
│   │   ├── gateway.py              # реализация IDiscordGateway
│   │   ├── client.py               # инициализация discord-клиента
│   │   └── test_harness.py         # моки для тестирования когов (единственный канонический способ)
│   ├── ai/
│   │   ├── anthropic_provider.py
│   │   ├── openai_provider.py
│   │   ├── resilient_provider.py   # retry/backoff/rate-limit
│   │   ├── circuit_breaker.py      # Circuit Breaker для AI
│   │   ├── cache_provider.py       # кэширование AI-ответов
│   │   └── prompts/                # шаблоны промптов с версионированием
│   ├── events/
│   │   ├── in_memory_bus.py        # синхронная реализация (MVP)
│   │   ├── redis_bus.py            # Redis Pub/Sub
│   │   └── outbox_bus.py           # публикация через Outbox
│   ├── cache/
│   │   ├── redis_client.py
│   │   └── decorators.py           # @cached, @cache_invalidate
│   ├── scheduler/
│   │   ├── scheduler.py            # фоновые задачи (APScheduler)
│   │   ├── ai_queue.py             # ограничение конкурентности запросов к AI
│   │   └── tasks.py                # конкретные задачи
│   ├── logging/
│   │   ├── json_formatter.py       # структурированное логирование
│   │   ├── context.py              # контекст логирования (correlation_id)
│   │   └── middleware.py           # логирование всех запросов
│   ├── web/
│   │   ├── app.py                  # aiohttp-приложение: /health, /ready, /metrics
│   │   └── metrics.py              # регистрация Prometheus-метрик
│   └── feature_flags/
│       ├── flags.py                # IFeatureFlags реализация
│       └── storage.py              # хранение флагов (Redis/Env)
│
├── config.py                       # класс Settings (Pydantic)
├── main.py                         # точка входа
└── exceptions.py                   # глобальные исключения
```

**Правило группировки:**
- Новая фича = новая подпапка с одинаковым именем в `domain/`, `application/` и `infrastructure/db/models`, `infrastructure/discord/cogs`.
- Общий код выносится в `domain/shared/` только после появления реального дублирования.

---

## 4. Выбор технологий и библиотек

| Компонент | Технология | Примечание |
|---|---|---|
| Язык | Python 3.11+ | asyncio, строгие type hints |
| Discord | discord.py | слеш-команды и интеракции |
| ORM | SQLAlchemy 2.0 async | asyncpg (PG), aiosqlite (SQLite) |
| Миграции | Alembic | batch mode обязателен |
| Настройки | pydantic-settings | .env + валидация |
| DTO | Pydantic | только в application/schemas |
| Домен | dataclasses (frozen) | не зависит от Pydantic |
| AI | абстракция IAIProvider | конкретные провайдеры в infra |
| Тесты | pytest + pytest-asyncio | unittest.mock для изоляции |
| Container | Docker + Docker Compose | PG, Redis, бот |
| DI | ручные фабрики | перейти на punq, когда ручная фабрика начинает дублировать граф зависимостей между фичами или требует ручной сортировки порядка создания — не по числу строк (число строк — плохой сигнал: он растёт с числом фич независимо от того, назрела ли проблема) |
| HTTP-сервер | aiohttp | `/health`, `/ready`, `/metrics` — отдельный таск внутри процесса бота, не отдельный сервис |
| Метрики | prometheus_client | экспозиция через aiohttp-эндпоинт `/metrics` |
| Логи | python-json-logger | структурированное логирование |
| Трассировка | opentelemetry | опционально для distributed tracing |

**Уточнение по HTTP-серверу:** health/metrics-эндпоинты — это `aiohttp.web.Application`, запускаемый как фоновая задача в том же event loop, что и discord-клиент (через `asyncio.gather` в `main.py`), а не отдельный процесс или фреймворк вроде FastAPI. Это осознанный выбор ради минимальных зависимостей — весь стек и так asyncio-native.

**Уточнение по лимитам Discord API:** discord.py самостоятельно обрабатывает per-route rate limit (HTTP 429) на уровне библиотеки — это не требует ручного кода. Проектная ответственность — не полагаться на то, что это покрывает всё:
- Массовые операции (бэкфилл ролей, рассылка по гильдиям, миграции данных) обязаны идти через `IDiscordGateway` с собственным ограничением частоты на стороне вызывающего кода (тот же `AIQueue`-подход через семафор/токен-бакет), а не полагаться на встроенный ретрай discord.py как единственную защиту.
- Каждый перехваченный `discord.HTTPException` с кодом 429 логируется структурированно и инкрементирует метрику `discord_rate_limited_total{route}` (раздел 9.2) — иначе деградация на этом уровне остаётся невидимой до жалоб пользователей.

**Единый источник конфигурации.** Ровно один файл `.env` управляет и локальным запуском без Docker, и всеми сервисами в `docker-compose.yml` (детали — раздел 13.2). Ни Dockerfile, ни `docker-compose.yml` не должны содержать захардкоженных значений настроек (диалект БД, хосты, ключи AI-провайдера) — только ссылки вида `env_file: .env`. Если для смены поведения бота требуется правка чего-то, кроме `.env`, это нарушение данного правила и повод пересмотреть, где осела настройка.

---

## 5. Работа с базой данных и миграции

### 5.1 Конфигурация

- Переключение между SQLite и PostgreSQL — через `DB_DIALECT` в `.env`.
- Асинхронный движок создаётся один раз при старте.
- Фабрика сессий `AsyncSessionLocal` передаётся в UOW и репозитории через конструктор.

### 5.2 ORM модели

- ORM-модели не используются за пределами инфраструктурного слоя.
- Мапперы преобразуют ORM → Domain → DTO.

### 5.3 Миграции

**Обязательное правило для Alembic:**
- В `env.py` включить `render_as_batch=True`.
- Все автогенерируемые миграции проверять на совместимость с SQLite.
- В dev-режиме автоприменение только при `AUTO_MIGRATE=true` с файловым локом.
- В production — только отдельной командой в deploy-пайплайне.

Пример миграции:

```python
# migrations/versions/xxx_add_user_table.py
def upgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('level', sa.Integer(), server_default='1'))
        batch_op.create_index('ix_users_level', ['level'])
```

---

## 6. Unit of Work, транзакции и критичность событий

### 6.1 Контракт

```python
# application/interfaces/unit_of_work.py
class IUnitOfWork(ABC):
    @abstractmethod
    async def __aenter__(self): ...

    @abstractmethod
    async def __aexit__(self, *args): ...

    @abstractmethod
    async def commit(self) -> None: ...

    @abstractmethod
    async def rollback(self) -> None: ...

    @property
    @abstractmethod
    def users(self) -> IUserRepository: ...

    @property
    @abstractmethod
    def transactions(self) -> ITransactionRepository: ...
```

### 6.2 Использование в use case

```python
# application/economy/use_cases/add_coins.py
class AddCoinsUseCase:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork]):
        self.uow_factory = uow_factory

    async def execute(self, command: AddCoinsCommand) -> AddCoinsResult:
        async with self.uow_factory() as uow:
            user = await uow.users.get(command.user_id)
            if not user:
                raise UserNotFoundError(command.user_id)

            user.add_coins(command.amount, command.reason)
            await uow.users.save(user)

            # Событие публикуется автоматически в UOW.commit() — см. 6.3
            await uow.commit()

            return AddCoinsResult(new_balance=user.balance)
```

### 6.3 Формализация критичности события — обязательный маркер, не соглашение

Решение "Outbox или прямая публикация" не может оставаться на усмотрение разработчика фичи — это источник расхождений между намерением архитектуры и фактическим кодом. Критичность фиксируется **типом события**, а не комментарием или memory-правилом:

```python
# domain/events/base.py
@dataclass(frozen=True)
class DomainEvent:
    event_id: UUID
    occurred_at: datetime
    aggregate_id: str
    event_type: str
    version: int = 1

@dataclass(frozen=True)
class CriticalDomainEvent(DomainEvent):
    """Наследоваться от этого класса, а не от DomainEvent, если потеря
    события недопустима для консистентности других фич (например,
    начисление/списание валюты, любое изменение баланса или прав доступа).
    UOW проверяет тип через isinstance и решает маршрут публикации
    автоматически — эта проверка не обходится вручную."""
```

```python
# domain/economy/events.py
@dataclass(frozen=True)
class CoinsEarned(CriticalDomainEvent):
    user_id: str
    amount: int
    reason: str

@dataclass(frozen=True)
class UserLeveledUp(DomainEvent):
    # Не критично для консистентности других фич — UX-уведомление (например, AI-поздравление).
    # Достаточно at-most-once; допустима потеря при падении процесса.
    user_id: str
    new_level: int
```

Ревью-чеклист для новой фичи (раздел 14) явно требует: для каждого нового события в PR указать, от какого базового класса оно наследуется, и почему.

### 6.4 Автоматическая публикация событий в UOW

При `commit()` UOW выполняет строго в этом порядке:

1. Сохраняет все изменения в БД (`session.flush()` + `session.commit()`).
2. Собирает все события, накопленные изменёнными агрегатами (паттерн `entity.pull_events()`).
3. Для каждого события проверяет тип:
   - `isinstance(event, CriticalDomainEvent)` → пишет строку в таблицу `outbox` **в той же транзакции**, что и бизнес-данные (шаг 1), и только после её успешного коммита — публикация тем не менее откладывается: сам outbox-relay вычитывает и публикует асинхронно (раздел 7.4).
   - иначе → публикует через `IEventBus.publish()` сразу после успешного `commit()` (не до).
4. Если шаг 1 упал — ни бизнес-данные, ни outbox-запись не сохраняются (атомарность гарантирована тем, что событие и данные пишутся в одной транзакции SQLAlchemy).

---

## 7. Событийная шина и гарантии доставки

### 7.1 Базовые типы

См. раздел 6.3 — `DomainEvent` и `CriticalDomainEvent`.

### 7.2 IEventBus

```python
# domain/events/bus.py
class IEventBus(ABC):
    @abstractmethod
    async def publish(self, event: DomainEvent) -> None: ...

    @abstractmethod
    def subscribe(self, event_type: Type[DomainEvent], handler: EventHandler) -> None: ...
```

### 7.3 Гарантии доставки

| Режим | Гарантия | Когда использовать |
|---|---|---|
| In-Memory | At-most-once | MVP, некритичные уведомления (`DomainEvent`) |
| Outbox | At-least-once (с идемпотентностью подписчика) | Критические изменения (`CriticalDomainEvent`) |
| Redis Pub/Sub | At-most-once | Межпроцессная коммуникация, некритичные события |
| Redis Streams | At-least-once | Промышленная нагрузка, критичные события |

### 7.4 Outbox паттерн

```python
# domain/events/outbox.py
@dataclass
class OutboxItem:
    id: UUID
    event_type: str
    payload: dict
    aggregate_id: str
    created_at: datetime
    processed_at: Optional[datetime] = None
    retry_count: int = 0
    last_error: Optional[str] = None
    dead_lettered_at: Optional[datetime] = None
```

**Outbox Relay:**
- Фоновый процесс, вычитывает необработанные события.
- Публикует их через `IEventBus`.
- Помечает как обработанные.
- Retry с exponential backoff — **но не бесконечно**: после `OUTBOX_MAX_RETRIES` (в `Settings`, по умолчанию 10) запись помечается dead-letter (`dead_lettered_at`), инкрементируется метрика `outbox_dead_letters_total` и запись перестаёт блокировать очередь. Без этой политики одно "ядовитое" событие (обработчик которого падает всегда) ретраится вечно и держит `outbox_queue_size` растущим.
- Dead-letter записи разбираются вручную или отдельной командой переобработки после исправления обработчика; на рост `outbox_dead_letters_total` настраивается алерт.
- Мониторинг размера очереди Outbox (метрика `outbox_queue_size`, раздел 9.2).

### 7.5 Обработчики событий

- Регистрируются в `application/<feature>/event_handlers/`.
- Подписчики `CriticalDomainEvent` (доставка через Outbox — at-least-once) обязаны быть идемпотентными: дедупликация по `event_id`. Для обычных `DomainEvent` (at-most-once) маркер обработанности не нужен — повторной доставки не бывает.
- "Не блокировать event loop" означает: никакого синхронного I/O внутри обработчика. Это **не** означает fire-and-forget через `asyncio.create_task` — обработчик выполняет работу через `await`, чтобы падение работы было видно доставке (и Outbox мог ретраить), а таска не потерялась из-за сборки мусора.
- Ошибки в одном обработчике логируются и не прерывают ни исходную транзакцию, ни доставку события остальным подписчикам (гарантия — раздел 10.3).

Пример идемпотентного обработчика критичного события:

```python
# application/economy/event_handlers/achievement_handler.py
class AchievementHandler:
    """Подписчик на CoinsEarned (CriticalDomainEvent, at-least-once)."""

    def __init__(self, processed_events_repo: IProcessedEventsRepo):
        self.processed_events_repo = processed_events_repo

    async def handle(self, event: CoinsEarned) -> None:
        # 1. Быстрая проверка дубля. Окончательная защита от гонки двух
        #    параллельных доставок — не здесь, а в unique constraint на
        #    processed_events.event_id (шаг 3).
        if await self.processed_events_repo.exists(event.event_id):
            return

        # 2. Сама работа — через await. Если она упадёт, событие останется
        #    непомеченным, и Outbox relay доставит его повторно.
        await self._grant_achievements(event)

        # 3. Маркер сохраняется ПОСЛЕ успешной работы. При падении между
        #    шагами 2 и 3 работа повторится — поэтому она сама должна быть
        #    идемпотентной (upsert, проверка существования) или терпимой
        #    к редкому дублю. Второй параллельный insert маркера падает
        #    на unique constraint и логируется, не прерывая доставку.
        await self.processed_events_repo.save(event.event_id)
```

Для обработчиков, чей побочный эффект нетранзакционен (отправка сообщения в Discord), порядок "работа -> маркер" сохраняется: at-least-once допускает редкий дубль сообщения, но не допускает молча потерянное событие, помеченное обработанным.

---

## 8. AI-модуль

### 8.1 Интерфейс

```python
# application/interfaces/ai_provider.py
class IAIProvider(ABC):
    @abstractmethod
    async def generate_response(self, prompt: str, context: dict) -> str: ...

    @abstractmethod
    async def generate_response_stream(self, prompt: str, context: dict) -> AsyncIterator[str]: ...

    @abstractmethod
    async def generate_embedding(self, text: str) -> List[float]: ...

    @abstractmethod
    async def is_available(self) -> bool: ...
```

### 8.2 Композиция провайдеров

```python
# infrastructure/ai/ai_factory.py
def create_ai_provider(settings: Settings) -> IAIProvider:
    provider = create_raw_provider(settings)  # Anthropic/OpenAI

    # Декораторы для дополнительных функций — порядок важен:
    # circuit breaker снаружи всех, чтобы размыкаться и на сбоях кэша/retry-обёрток
    if settings.REDIS_URL:
        provider = CachedAIProvider(provider, redis_client)

    if settings.AI_RETRY_ENABLED:
        provider = ResilientAIProvider(provider)

    if settings.AI_CIRCUIT_BREAKER_ENABLED:
        provider = CircuitBreakerAIProvider(
            provider,
            failure_threshold=settings.AI_CB_FAILURE_THRESHOLD,
            timeout=settings.AI_CB_TIMEOUT_SECONDS,
        )

    return provider
```

### 8.3 Circuit Breaker

Два обязательных свойства реализации:
1. **Счётчик сбоев сбрасывается при любом успехе, включая состояние CLOSED.** Иначе редкие несвязанные сбои накапливаются неделями и в итоге ложно размыкают цепь на исправном провайдере.
2. **Брейкер оборачивает все методы `IAIProvider`**, а не только `generate_response` — сбой embedding- или stream-запросов деградирует провайдера так же, как сбой обычного чата.

```python
# infrastructure/ai/circuit_breaker.py
class CircuitBreakerAIProvider(IAIProvider):
    def __init__(self, provider: IAIProvider, failure_threshold: int = 5, timeout: int = 60):
        self.provider = provider
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.last_failure_time: Optional[float] = None

    def _before_call(self) -> None:
        if self.state == "OPEN":
            if time.time() - self.last_failure_time > self.timeout:
                self.state = "HALF_OPEN"
            else:
                raise AIUnavailableError("Circuit breaker is OPEN")

    def _on_success(self) -> None:
        # Сброс при ЛЮБОМ успехе, не только при HALF_OPEN -> CLOSED
        self.state = "CLOSED"
        self.failure_count = 0

    def _on_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        # Неудачная проба в HALF_OPEN размыкает цепь сразу, без накопления порога
        if self.state == "HALF_OPEN" or self.failure_count >= self.failure_threshold:
            self.state = "OPEN"

    async def generate_response(self, prompt: str, context: dict) -> str:
        self._before_call()
        try:
            result = await self.provider.generate_response(prompt, context)
        except Exception:
            self._on_failure()
            raise
        self._on_success()
        return result

    async def generate_embedding(self, text: str) -> List[float]:
        self._before_call()
        try:
            result = await self.provider.generate_embedding(text)
        except Exception:
            self._on_failure()
            raise
        self._on_success()
        return result

    async def generate_response_stream(self, prompt: str, context: dict) -> AsyncIterator[str]:
        # Успех фиксируется только после ПОЛНОГО прочтения стрима:
        # обрыв посреди генерации — это тоже сбой провайдера.
        self._before_call()
        try:
            async for chunk in self.provider.generate_response_stream(prompt, context):
                yield chunk
        except Exception:
            self._on_failure()
            raise
        self._on_success()

    async def is_available(self) -> bool:
        return self.state != "OPEN" and await self.provider.is_available()
```

### 8.4 Rate Limiting

Ограничение конкурентности (глобальное, через семафор) и ограничение частоты запросов одним пользователем (per-user) — это две разные проблемы, и решаются двумя разными компонентами: `AIQueue` защищает провайдера от перегрузки в целом, `IRateLimiter` защищает очередь от монополизации одним пользователем. Без второго один активный пользователь может занять весь `max_concurrent` и заблокировать AI-фичу для всех остальных.

```python
# application/interfaces/rate_limiter.py
class IRateLimiter(ABC):
    @abstractmethod
    async def acquire(self, key: str) -> None:
        """Бросает RateLimitExceededError, если квота для key исчерпана."""
        ...
```

```python
# infrastructure/scheduler/ai_queue.py
class AIQueue:
    """Ограничивает конкурентность запросов к AI-провайдеру через семафор
    (глобально) и частоту запросов на пользователя через IRateLimiter.
    Семафор — не FIFO-очередь: запросы сверх max_concurrent просто ждут
    освобождения слота в порядке поступления к await. IRateLimiter —
    отдельный порт (реализация in-memory или Redis — infrastructure/cache/),
    чтобы политика лимита (сколько запросов в минуту на пользователя)
    не была захардкожена в этом классе."""

    def __init__(self, provider: IAIProvider, rate_limiter: IRateLimiter, max_concurrent: int = 5):
        self.provider = provider
        self.rate_limiter = rate_limiter
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def process_request(self, user_id: int, prompt: str, context: dict) -> str:
        await self.rate_limiter.acquire(str(user_id))
        async with self.semaphore:
            return await self.provider.generate_response(prompt, context)
```

Конкретные значения лимита (запросов в минуту/час на пользователя) выносятся в `Settings` (`AI_PER_USER_RATE_LIMIT`), не хардкодятся.

### 8.5 Система уровней отношений (relationship)

Персона бота (см. системный промпт Попоси) требует переменных `{{relationship_level}}` (1–7), `{{is_exclusive_person}}` и `{{current_date}}`. Их вычисляет и хранит отдельная фича `relationship` — полноценная фича по алгоритму раздела 14, а не деталь `ai_chat`.

**Домен (`domain/relationship/`):**
- `RelationshipProfile` — сущность: `user_id`, `guild_id`, `points` (целое, дефолт 0), `points_awarded_today` + `last_award_date` (для дневного потолка), `frozen_by_admin`, `last_dialog_at`, `user_notes` (см. ниже). Уровень и роль **не хранятся** — детерминированно вычисляются из очков (и лидерства для «Единственного»), поэтому рассинхрона между полем и фактом не бывает.
- **Начисление очков**: +1 очко за каждое сообщение, адресованное персоне (упоминание или реплай), но не больше `RELATIONSHIP_DAILY_POINT_CAP` (20 по умолчанию) в сутки — защита от накрутки: максимальный статус требует минимум ~18 дней реального общения. Профиль с `frozen_by_admin=true` очков не получает (админ-заморозка).
- `PointsToLevelPolicy` — маппинг очков в роль и тон промпта; пороги и имена ролей в `Settings` (`RELATIONSHIP_ROLE_THRESHOLDS`, `RELATIONSHIP_ROLE_NAMES`):

| Очки | Роль (сет «Дождливый Токио») | Тон промпта |
|---|---|---|
| 0–49 | — (без роли) | 1 |
| 50 | ☕ Случайный прохожий | 2 |
| 100 | 🌧 Знакомый силуэт | 3 |
| 150 | 🎨 Занятный собеседник | 4 |
| 200 | 🎧 На одной волне | 5 |
| 250 | 🍷 Вечерняя компания | 6 |
| 300 | 🖤 Особенный | 6 (престиж-ступень) |
| 350+ и лидер сервера | ✂️👁🖤 Единственный | 7 |
- **Возвращение после перерыва**: уровень при отсутствии не падает, но если с `last_dialog_at` прошло больше `RELATIONSHIP_ABSENCE_DAYS` (30 по умолчанию), первые `RELATIONSHIP_COLD_DIALOGS` (2) диалога промпт получает флаг `{{returning_after_absence}}` — персона ведёт себя на ступень холоднее. Состояние БД не трогается, только рендер промпта.
- **Заметки о пользователе (`user_notes`)** — краткая память фактов (имя, интересы, прошлые темы; лимит `RELATIONSHIP_NOTES_MAX_CHARS`, ~700). После завершения диалога отдельный дешёвый AI-вызов обновляет заметку (старая заметка + свежий диалог → новая заметка); заметка подставляется в промпт как `{{user_notes}}`. Без векторных БД — осознанно, до реальной потребности.
- `ExclusivityRule` — «✂️👁🖤 Единственный» один на сервер и принадлежит **текущему лидеру по очкам** с очками ≥350. Титул переходит, только когда претендент **строго превышает** очки держателя — защита от мигания роли при равенстве. Очки в текущей версии не убывают, поэтому смена происходит при обгоне; если позже добавится угасание очков, правило лидерства уже его покрывает. Лидерство пересчитывается при каждом начислении очков в той же транзакции UOW.
- События: `RelationshipRoleChanged` (смена роли-ступени), `ExclusiveTransferred` (переход титула, в payload старый и новый держатель) — оба **`DomainEvent`, не Critical**: источник истины — очки в БД, а выдача Discord-ролей самовосстанавливается сверкой (см. «Discord-роли» ниже), поэтому потеря события не создаёт постоянного рассинхрона. Это первый показательный кейс для пункта ревью-чеклиста об обосновании базового класса события.

**Application (`application/relationship/`):**
- `AwardPointUseCase` — вызывается на каждое адресованное персоне сообщение: проверяет дневной потолок, начисляет очко, пересчитывает роль (`PointsToLevelPolicy`) и лидерство (`ExclusivityRule`); при смене роли публикует `RelationshipRoleChanged`, при смене лидера — `ExclusiveTransferred`. Всё в одной транзакции UOW.
- `SetPointsUseCase`, `FreezePointsUseCase` — админские операции: ручная коррекция очков (после неё тот же пересчёт роли и лидерства) и заморозка начисления.

**Чтение уровня из `ai_chat`.** Фичи не вызывают use cases друг друга, но читающая зависимость через порт разрешена: `IRelationshipReader.get_profile(user_id, guild_id)` в `application/interfaces/`, реализация — в `infrastructure/db/`. `ai_chat` использует порт при сборке промпта: `PromptTemplate` подставляет `{{relationship_level}}`, `{{is_exclusive_person}}`, `{{current_date}}`, `{{user_notes}}`, `{{returning_after_absence}}`. Это уточнение к правилу «только через события»: события развязывают **команды** между фичами; синхронное чтение состояния идёт через абстракцию и связности реализаций не создаёт.

**Discord-роли.** Роли-статусы создаются ботом при первом запуске в гильдии (имена из `RELATIONSHIP_ROLE_NAMES`, по умолчанию сет из таблицы выше); боту нужно право Manage Roles, и его собственная роль должна стоять выше ролей-статусов. Выдача и снятие — обработчики `RelationshipRoleChanged` / `ExclusiveTransferred` в инфраструктурном слое. Поскольку события некритичные, обязательна **самовосстанавливающаяся сверка**: при каждом начислении очков фактическая Discord-роль пользователя сверяется с вычисленной по очкам и исправляется при расхождении — потерянное событие лечится следующим же сообщением пользователя.

**Триггеры общения `ai_chat` (первая версия):**
1. **Реактивный** — упоминание бота или реплай на его сообщение; в промпт идут последние N сообщений канала как контекст.
2. **Событийный** — обработчики событий шины: `TrackStarted` (комментарий к включённому треку), `RelationshipRoleChanged` и `ExclusiveTransferred`. Обязательно дозирование: шанс срабатывания (`AI_EVENT_COMMENT_CHANCE`, ~0.12) и cooldown на канал (`AI_EVENT_COMMENT_COOLDOWN`) — комментарий к каждому событию убивает эффект присутствия.
3. **Проактивный** (сама вступает в разговор / пишет первой на высоких уровнях) — в первую версию не входит; закладывается фиче-флагом `FEATURE_AI_PROACTIVE` и отдельным обработчиком, существующий код при включении не правится.

**Смена роли — реплика, не уведомление.** Обработчик `RelationshipRoleChanged` в `ai_chat` генерирует органичную фразу в характере персоны («Знаешь, ты перестал меня раздражать»), никаких эмбедов «🎉 Новая роль». Сама Discord-роль выдаётся молча. Переход «Единственного» персона может отметить одной фразой — и новому держателю, и бывшему. Персона никогда не называет очки, пороги и механику явно.

**Квота общения зависит от уровня.** `IRateLimiter` (раздел 8.4) получает лимит из профиля: `AI_RATE_LIMITS_BY_LEVEL` в `Settings` (например, ур.1 — 5 реплик/час, ур.5+ — практически без лимита). Близость = доступ: игровая механика и контроль расходов на токены одновременно.

**DM (личные сообщения)** — в первой версии отключены, но появятся позже: профиль уже ключуется `(user_id, guild_id)`, при включении DM потребуется правило выбора «домашней» гильдии профиля — решение фиксируется отдельным ADR на этапе реализации DM, текущую модель данных оно не ломает.

**Discord (`infrastructure/discord/cogs/relationship/`):** админ-команды `/relationship points <user> <число>` и `/relationship freeze <user>` — права только у администратора гильдии. Публичная `/rank` — свои очки, текущая роль и сколько осталось до следующей.

---

## 9. Метрики и наблюдаемость

### 9.1 Структурированное логирование

```python
# infrastructure/logging/context.py
import contextvars

_correlation_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "correlation_id", default=None
)

class LoggingContext:
    @staticmethod
    @contextmanager
    def correlation_id(value: UUID):
        token = _correlation_id.set(str(value))
        try:
            yield
        finally:
            _correlation_id.reset(token)

    @staticmethod
    def get() -> Optional[str]:
        return _correlation_id.get()
```

```python
# Использование (JSON-форматтер логов читает LoggingContext.get() и добавляет поле в каждую запись):
async def execute_use_case(self, command):
    with LoggingContext.correlation_id(uuid4()):
        logger.info("Executing use case", command=command)
        # correlation_id автоматически попадёт в структурированный лог
```

**Важно:** используется `contextvars`, а не classvar-словарь, как в черновом варианте — это обязательно для asyncio, где параллельные задачи (`asyncio.create_task`) не должны делить общий мутируемый словарь контекста; `contextvars` копирует контекст в каждую задачу изолированно.

**Правило по секретам в логах:** JSON-форматтер (`json_formatter.py`) обязан фильтровать поля `*_API_KEY`, `*_TOKEN`, `PASSWORD`, `DATABASE_URL` (маскировать значение, если ключ лога совпадает по паттерну) до сериализации записи. Это отдельная гарантия от требования раздела 13 "секреты не хардкодятся" — там речь про конфигурацию при сборке, здесь — про то, что секрет, один раз попавший в `Settings`, не должен случайно утечь через `logger.info(command=...)` или трассировку исключения.

### 9.2 Метрики

```python
# infrastructure/web/metrics.py
from prometheus_client import Counter, Histogram, Gauge

USE_CASE_DURATION = Histogram(
    'use_case_duration_seconds',
    'Use case execution duration',
    ['use_case', 'success']
)

EVENTS_PUBLISHED = Counter(
    'events_published_total',
    'Total events published',
    ['event_type']
)

OUTBOX_QUEUE_SIZE = Gauge(
    'outbox_queue_size',
    'Number of pending outbox events'
)

AI_REQUEST_DURATION = Histogram(
    'ai_request_duration_seconds',
    'AI provider request duration',
    ['provider', 'model']
)

DISCORD_RATE_LIMITED = Counter(
    'discord_rate_limited_total',
    'Intercepted Discord HTTP 429 responses',
    ['route']
)

OUTBOX_DEAD_LETTERS = Counter(
    'outbox_dead_letters_total',
    'Outbox events moved to dead-letter after retry exhaustion'
)
```

### 9.3 Health Checks (aiohttp, infrastructure/web)

```python
# infrastructure/web/app.py
from aiohttp import web

class HealthChecker:
    def __init__(self):
        self.checks: Dict[str, Callable[[], Awaitable[bool]]] = {}

    def register(self, name: str, check: Callable[[], Awaitable[bool]]):
        self.checks[name] = check

    async def check(self) -> dict:
        results = {}
        for name, check_fn in self.checks.items():
            try:
                results[name] = await check_fn()
            except Exception:
                results[name] = False
        return results

def create_web_app(health_checker: HealthChecker) -> web.Application:
    app = web.Application()

    async def health_handler(request: web.Request) -> web.Response:
        status = await health_checker.check()
        if all(status.values()):
            return web.json_response({"status": "healthy", "checks": status})
        return web.json_response({"status": "unhealthy", "checks": status}, status=503)

    app.router.add_get("/health", health_handler)
    return app

# main.py: запускается как фоновая задача рядом с discord-клиентом через
# aiohttp.web.AppRunner + TCPSite внутри общего asyncio.gather(...), не как
# отдельный процесс/фреймворк.
```

---

## 10. Тестирование

### 10.1 Уровни тестирования

| Уровень | Что тестируем | Инструменты |
|---|---|---|
| Unit | Доменные сущности, use cases | Моки репозиториев, in-memory шина |
| Integration | Репозитории, миграции | SQLite in-memory, testcontainers |
| Event | Шина событий, Outbox | In-memory bus, фейковый relay |
| E2E | Коги, полноценные сценарии | `test_harness.py` (единственный канонический способ, см. 10.2) |

### 10.2 Test Harness для когов — единственный канонический подход

Все тесты когов используют моки из `infrastructure/discord/test_harness.py`. Отдельного класса-обёртки для когов (`MockCog` и подобные) не заводится — use case передаётся в конструктор реального класса кога напрямую, как в production DI, чтобы тест проверял тот же путь вызова, что и боевой код.

**Обязательное правило:** контракт моков зеркалит публичный API discord.py (`interaction.response.send_message()`, `interaction.response.defer()`, `interaction.followup.send()`). Мок с "удобным", но несуществующим методом (вроде `interaction.respond()` — такого в discord.py нет) даёт зелёные тесты для кога, который упадёт в бою.

```python
# infrastructure/discord/test_harness.py
class MockInteractionResponse:
    def __init__(self):
        self.messages: list[str] = []
        self.kwargs: dict = {}
        self.deferred = False

    async def send_message(self, content: str = None, **kwargs):
        self.messages.append(content)
        self.kwargs = kwargs

    async def defer(self, **kwargs):
        self.deferred = True


class MockFollowup:
    def __init__(self):
        self.messages: list[str] = []

    async def send(self, content: str = None, **kwargs):
        self.messages.append(content)


class MockInteraction:
    def __init__(self, user_id: int, channel_id: int):
        self.user = MockUser(user_id)
        self.channel = MockChannel(channel_id)
        self.response = MockInteractionResponse()
        self.followup = MockFollowup()
```

```python
# tests/e2e/test_level_cog.py — использование в тестах:
async def test_level_command():
    # Given
    interaction = MockInteraction(user_id=123, channel_id=456)
    level_service = MockLevelService(level=5)
    cog = LevelCog(level_service=level_service)  # реальный класс кога, как в DI

    # When
    await cog.level_command(interaction)

    # Then
    assert "Your level is 5" in interaction.response.messages
```

### 10.3 Тесты Event Bus (изоляция ошибок подписчиков)

```python
async def test_event_bus_error_isolation():
    bus = InMemoryEventBus()
    errors: list[Exception] = []
    success_handler_called = False

    async def failing_handler(event):
        raise ValueError("Handler failed")

    async def success_handler(event):
        nonlocal success_handler_called
        success_handler_called = True

    # Шина обязана перехватывать исключение подписчика сама и логировать его —
    # это и есть контракт "падение одного подписчика не мешает остальным".
    # В тесте подменяем логгер шины, чтобы зафиксировать перехваченные ошибки.
    bus.on_handler_error = lambda event, handler, exc: errors.append(exc)

    bus.subscribe(TestEvent, failing_handler)
    bus.subscribe(TestEvent, success_handler)

    await bus.publish(TestEvent(event_id=uuid4(), occurred_at=datetime.utcnow(),
                                  aggregate_id="test", event_type="TestEvent"))

    assert success_handler_called
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
```

Соответственно `InMemoryEventBus.publish()` обязан оборачивать вызов каждого подписчика в `try/except` и передавать исключение в `on_handler_error` (по умолчанию — просто структурированный `logger.error(...)`), а не пробрасывать его наружу.

---

## 11. Feature Flags

```python
# application/interfaces/feature_flags.py
class IFeatureFlags(ABC):
    @abstractmethod
    def is_enabled(self, feature: str, user_id: Optional[int] = None) -> bool: ...
```

```python
# infrastructure/feature_flags/flags.py
import hashlib
import os

class EnvironmentFeatureFlags(IFeatureFlags):
    def __init__(self, env_prefix: str = "FEATURE_"):
        self.prefix = env_prefix

    def is_enabled(self, feature: str, user_id: Optional[int] = None) -> bool:
        env_var = f"{self.prefix}{feature.upper()}"
        value = os.getenv(env_var, "false").lower()

        if value.startswith("percent:"):
            if user_id is None:
                return False  # процентный rollout без пользователя не имеет смысла
            percent = int(value.split(":")[1])
            # НЕ user_id % 100: Discord snowflake в младших битах содержит
            # worker/process id и инкремент — распределение по модулю
            # неравномерно. Хэш от (фича, user_id) даёт равномерные и
            # независимые между фичами бакеты.
            digest = hashlib.sha256(f"{feature}:{user_id}".encode()).hexdigest()
            return int(digest, 16) % 100 < percent

        return value in ("true", "1", "yes")
```

```python
# Использование:
if feature_flags.is_enabled("ai_chat", ctx.author.id):
    await ai_use_case.execute(...)
else:
    await legacy_use_case.execute(...)
```

---

## 12. Graceful Shutdown

```python
# main.py
async def shutdown(sig, loop, bot, ai_queue, outbox_relay, engine, redis_client):
    logger.info("Received signal", signal=str(sig))

    # 1. Перестать принимать новые интеракции, НЕ разрывая gateway-соединение.
    #    Реализация — глобальный interaction_check / флаг, отклоняющий новые
    #    команды. bot.close() здесь вызывать нельзя: обработчикам на шагах
    #    2-3 ещё может понадобиться отправка сообщений в Discord.
    bot.reject_new_interactions()

    # 2. Дождаться завершения текущих AI-запросов
    await ai_queue.drain()

    # 3. Опубликовать оставшиеся outbox-события — их обработчики ещё могут
    #    пользоваться живым Discord-соединением
    await outbox_relay.flush()

    # 4. Теперь закрыть Discord-соединение
    await bot.close()

    # 5. Закрыть соединения с БД (у async_sessionmaker нет close_all —
    #    закрывается движок)
    await engine.dispose()

    # 6. Закрыть Redis
    await redis_client.close()

    logger.info("Shutdown complete")
    loop.stop()

# Регистрация обработчиков сигналов в main():
for sig in (signal.SIGINT, signal.SIGTERM):
    loop.add_signal_handler(
        sig,
        lambda s=sig: asyncio.create_task(
            shutdown(s, loop, bot, ai_queue, outbox_relay, engine, redis_client)
        ),
    )
```

Порядок шагов 1–6 обязателен: сначала прекращается приём новых команд, но gateway-соединение остаётся живым — сливаемые на шагах 2–3 AI-очередь и Outbox могут порождать отправку сообщений в Discord; соединение закрывается только после этого (шаг 4); БД и Redis — последними, чтобы шаги 2–4 могли ими пользоваться. `reject_new_interactions()` — проектный флаг (например, глобальный `bot.check`/`interaction_check`, возвращающий отказ), а не метод discord.py.

---

## 13. Сборка и деплой

### 13.1 Dockerfile (многостадийный)

**Правило:** образ никогда не содержит `.env` и не знает значений конфигурации на этапе сборки — только код и зависимости. Конфигурация подставляется исключительно во время запуска контейнера (через `env_file` в compose, `docker run --env-file`, или секреты оркестратора). Это прямое следствие правила раздела 4 ("Единый источник конфигурации": секреты и настройки приходят только через `.env`/переменные окружения, никогда не коммитятся и не хардкодятся) — если бы `.env` копировался в образ, это правило нарушалось бы уже на уровне сборки.

```dockerfile
# Stage 1: Builder
FROM python:3.11-slim AS builder
WORKDIR /app
RUN pip install poetry
COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.in-project true
RUN poetry install --no-root --only main

# Stage 2: Runtime
FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY src/ ./src/
# .env НЕ копируется — конфигурация приходит только снаружи контейнера.
# pydantic-settings в config.py читает переменные окружения процесса;
# .env.example остаётся только в репозитории как шаблон для разработчика.
ENV PATH="/app/.venv/bin:$PATH"
CMD ["python", "-m", "src.main"]
```

### 13.2 Docker Compose

**Правило:** единственный источник настроек для всех сервисов — файл `.env` рядом с `docker-compose.yml` (в `.gitignore`, рядом лежит закоммиченный `.env.example`). Ничего специфичного для конфигурации (диалект БД, URL Redis, ключи AI-провайдера) не хардкодится в самом compose-файле — иначе смена настройки потребует правки инфраструктурного YAML, а не только `.env`, что противоречит критерию раздела 15 ("смена `DB_DIALECT` требует только правки конфига").

```yaml
version: '3.8'
services:
  db:
    image: postgres:15
    env_file: .env   # POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB — из .env
    volumes:
      - pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  bot:
    build: .
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    env_file: .env   # DB_DIALECT, DATABASE_URL, REDIS_URL, AI_*_API_KEY — всё из одного файла
    volumes:
      - ./src:/app/src  # только для разработки (live-reload); в проде эта строка убирается
    ports:
      - "8080:8080"  # aiohttp: /health, /ready, /metrics

volumes:
  pg_data:
```

```dotenv
# .env.example — единый файл для всех сервисов compose и для локального запуска без Docker.
# Для Docker-профиля значения по умолчанию таковы:
DB_DIALECT=postgresql
POSTGRES_USER=bot
POSTGRES_PASSWORD=change_me
POSTGRES_DB=bot
DATABASE_URL=postgresql+asyncpg://bot:change_me@db:5432/bot
REDIS_URL=redis://redis:6379

# Для запуска без Docker (локальная разработка, SQLite) — переопределить:
# DB_DIALECT=sqlite
# DATABASE_URL=sqlite+aiosqlite:///./dev.db
# REDIS_URL= (пусто — кэш отключён)

AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=
AUTO_MIGRATE=false
LOG_LEVEL=INFO
```

Это восстанавливает исходное требование "Единый `.env` для всех сервисов" буквально: и `db`, и `bot` читают один и тот же файл через `env_file: .env`, а не получают частично захардкоженные, частично файловые настройки вперемешку.

### 13.3 CI/CD (GitHub Actions)

```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        db: [sqlite, postgres]
    services:
      # Сервис поднимается для обеих ног матрицы: GitHub Actions не умеет
      # условные services per-matrix. Нога sqlite контейнер не использует.
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: test
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install poetry
      - run: poetry install
      - run: poetry run pytest
        env:
          DB_DIALECT: ${{ matrix.db }}
          # Имя переменной то же, что и в рантайме (DATABASE_URL), — тесты
          # конфигурируются тем же Settings, без параллельной схемы имён.
          DATABASE_URL: ${{ matrix.db == 'postgres' && 'postgresql+asyncpg://postgres:test@localhost:5432/postgres' || 'sqlite+aiosqlite:///./test.db' }}
```

---

## 14. Добавление новых фич — пошаговый алгоритм и ревью-чеклист

**Домен:**
1. Создать `domain/<feature>/` с entities, value_objects, rules.
2. Определить абстрактный репозиторий, если нужен.
3. Определить доменные события — **для каждого явно выбрать базовый класс: `DomainEvent` или `CriticalDomainEvent`** (раздел 6.3).

**Application:**
4. Создать use case в `application/<feature>/use_cases/`.
5. Создать DTO схемы.
6. Написать юнит-тесты use case.

**Инфраструктура:**
7. Реализовать репозиторий в `infrastructure/db/repositories/`.
8. Создать ORM модель.
9. Сгенерировать миграцию (проверить batch mode).
10. Написать интеграционный тест.

**События:**
11. Создать обработчики в `application/<feature>/event_handlers/`, идемпотентные и неблокирующие.
12. Зарегистрировать подписки в DI.

**Discord:**
13. Создать cog в `infrastructure/discord/cogs/<feature>/`.
14. Использовать use case из пункта 4.
15. Написать тест кога через `test_harness.py` (раздел 10.2).

**DI:**
16. Зарегистрировать зависимости в `application/<feature>/di.py`.
17. Подключить в `root_container.py`.

**Feature Flag (опционально):**
18. Добавить флаг для безопасного rollout.

**Метрики:**
19. Добавить метрики для use case и событий.

**Ревью-чеклист PR (обязателен для мерджа):**
- [ ] Ни один файл существующей фичи не изменён (кроме регистрации в `root_container.py` и подписки на событие).
- [ ] Для каждого нового события в PR указано и обосновано: `DomainEvent` или `CriticalDomainEvent`.
- [ ] Обработчики новых событий идемпотентны и не выполняют блокирующий I/O синхронно.
- [ ] Миграция проверена на SQLite и PostgreSQL в CI-матрице.

---

## 15. Критерии успеха

**Базовые:**
- ✅ Бот запускается локально и в Docker без изменений в коде.
- ✅ Смена `DB_DIALECT` требует только правки конфига.
- ✅ Use case тестируется без БД, Discord и AI.
- ✅ Новая фича не требует изменений в существующих файлах.
- ✅ Смена AI-провайдера — правка одной точки в DI.
- ✅ Docker сборка < 2 минут.
- ✅ Миграции работают на обеих СУБД.
- ✅ Падение одного подписчика не влияет на других (тест раздела 10.3 проходит).

**Дополнительные (для production):**
- ✅ Health checks корректно отражают состояние системы.
- ✅ Graceful shutdown завершает все операции в порядке раздела 12.
- ✅ Outbox гарантирует доставку критичных событий (`CriticalDomainEvent`).
- ✅ Circuit Breaker защищает от сбоев AI: покрывает все методы `IAIProvider`, сбрасывает счётчик при любом успехе (раздел 8.3).
- ✅ "Ядовитое" outbox-событие уходит в dead-letter после `OUTBOX_MAX_RETRIES` и не блокирует очередь.
- ✅ Feature flags позволяют безопасный rollout.
- ✅ Метрики дают представление о работе системы.
- ✅ Логирование содержит `correlation_id` для трассировки, устойчивое к параллельным asyncio-задачам (`contextvars`).
- ✅ Rate limiting защищает от перегрузки AI.

---

## 16. План развития (Roadmap)

**Phase 1: Foundation (недели 1–2)**
- Poetry, структура каталогов.
- Config, логирование (включая `contextvars`-based `LoggingContext`).
- Базовая БД (движок, фабрика сессий).
- In-memory Event Bus с обязательной изоляцией ошибок подписчиков (раздел 10.3) — реализуется сразу, не откладывается.
- `DomainEvent` / `CriticalDomainEvent` — базовые классы вводятся с первого события.
- Первая ORM модель.
- Alembic (batch mode).
- Первый доменный сервис + тесты.

**Phase 2: Core Features (недели 3–4)**
- Unit of Work с автопубликацией событий (раздел 6.4).
- Outbox таблица, репозиторий и relay — вводятся на первом же `CriticalDomainEvent`.
- Discord gateway (cogs, команды), `test_harness.py`.
- Первая фича (регистрация пользователя).
- Вторая фича (экономика) через события — проверка независимости фич.
- Docker + Docker Compose.

**Phase 3: Production Readiness (недели 5–6)**
- Health checks (`infrastructure/web/app.py`, aiohttp).
- Метрики (Prometheus).
- Graceful shutdown.
- Redis кэш.
- Feature flags.
- CI/CD пайплайн.

**Phase 4: AI Integration (недели 7–8)**
- `IAIProvider` интерфейс.
- Anthropic/OpenAI провайдеры.
- Circuit Breaker (по контракту раздела 8.3: все методы, сброс счётчика при успехе).
- `AIQueue` (семафор) и rate limiting.
- Кэширование AI ответов.
- Фича relationship (раздел 8.5): профиль, автопрогрессия с админ-override, инвариант эксклюзивности, порт `IRelationshipReader`.
- Первая AI-фича (поздравления) — с самого начала неблокирующая обработка событий.

**Phase 5: Scaling (недели 9–10)**
- Redis Pub/Sub для некритичных событий.
- Outbox relay оптимизация, Redis Streams — если понадобится at-least-once для межпроцессной шины.
- Профилирование и оптимизация.
- Документация для разработчиков.

---

## 17. Документация и онбординг

### 17.1 README.md
- Быстрый старт (локально и через Docker).
- Структура проекта.
- Как добавить новую фичу (ссылка на раздел 14, включая ревью-чеклист).
- Тестирование.
- Деплой.

### 17.2 ADR (Architecture Decision Records)

```markdown
# ADR-001: Выбор Event Bus и гарантий доставки (DomainEvent vs CriticalDomainEvent)
# ADR-002: Outbox паттерн для критичных событий
# ADR-003: Использование SQLAlchemy для ORM
# ADR-004: Unit of Work для управления транзакциями
# ADR-005: aiohttp для health/metrics вместо отдельного веб-фреймворка
```

### 17.3 API-документация
- Pydantic-схемы.
- Use cases.
- Доменные события с явным указанием базового класса (`DomainEvent` / `CriticalDomainEvent`).

---

Документ служит архитектурным стандартом проекта. Главные инварианты, которые нельзя нарушать по мере роста бота: направление зависимостей внутрь домена, отсутствие прямых вызовов между фичами (только через события), изоляция всех внешних систем (БД, Discord, AI, кэш) за портами, обязательный явный выбор `DomainEvent`/`CriticalDomainEvent` для каждого нового события, и порядок операций graceful shutdown из раздела 12.
