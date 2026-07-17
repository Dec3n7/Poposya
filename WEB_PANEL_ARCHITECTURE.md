# WEB_PANEL_ARCHITECTURE.md

> Версия: 1.0
> Статус: Architectural Specification
> Цель: Создать веб-панель, которая сможет развиваться вместе с Discord-ботом многие годы без дублирования бизнес-логики.

---

# Основная идея

Веб-панель **не является отдельным проектом**.

Она является **вторым клиентом**, использующим ту же бизнес-логику, что и Discord-бот.

Это позволяет:

- не дублировать код;
- иметь одну реализацию бизнес-логики;
- легко добавлять новые интерфейсы;
- поддерживать проект долгие годы.

---

# Архитектура

                   Discord Bot
                        │
                        ▼
                 Presentation Layer
                        │
                        ▼
                 Application Layer
                        │
                        ▼
                   Domain Layer
                        ▲
                        │
                Infrastructure Layer
                ▲                  ▲
                │                  │
          PostgreSQL            Redis
                ▲
                │
             REST API
                ▲
                │
         React Web Panel

Главный принцип:

> Discord и Web ничего не знают друг о друге.

Они используют одинаковые UseCase.

---

# Структура проекта

project/

    apps/
        discord/
        api/
        web/

    features/

        profile/

        moderation/

        music/

        relationships/

        economy/

        ai/

    infrastructure/

    shared/

    docs/

    tests/

---

# Принцип разработки

Любая новая функциональность создается один раз.

После этого она становится доступна:

- Discord
- Web
- REST API
- будущим приложениям

без переписывания бизнес-логики.

---

# Backend

Использовать:

- FastAPI
- Pydantic
- SQLAlchemy Async
- Alembic
- PostgreSQL
- Redis

Backend является единственным источником бизнес-логики.

---

# Frontend

Использовать:

- React
- TypeScript
- Vite

Frontend содержит только:

- отображение данных;
- формы;
- навигацию;
- вызовы REST API.

Никакой бизнес-логики.

---

# Авторизация

Использовать только Discord OAuth2.

После авторизации пользователь идентифицируется по Discord ID.

Никаких собственных логинов и паролей.

---

# Проверка прав

Все проверки выполняются только Backend.

Frontend никогда не решает:

- является ли пользователь владельцем;
- имеет ли права;
- может ли изменить настройки.

Frontend только отображает ответ API.

---

# API

REST API строится вокруг предметной области.

Хорошо

/api/guilds/{guild_id}/music

/api/guilds/{guild_id}/settings

/api/guilds/{guild_id}/members

/api/guilds/{guild_id}/relationships

Плохо

/play

/stop

/pause

/set_volume

API должен описывать предметную область, а не действия интерфейса.

---

# Роутеры

Каждый Feature имеет собственный Router.

Например

api/

    profile/

        router.py

    music/

        router.py

    moderation/

        router.py

Никаких огромных api.py.

---

# Структура Feature

Каждый модуль должен иметь одинаковую структуру.

feature/

    domain/

    application/

    infrastructure/

    presentation/

Каждый слой имеет одну ответственность.

---

# Use Cases

Вся бизнес-логика находится только здесь.

Примеры

CreateProfileUseCase

PlayTrackUseCase

CreateRelationshipUseCase

BanMemberUseCase

UseCase отвечает только за одну операцию.

---

# Repository

Repository отвечает исключительно за хранение данных.

Repository не должен:

- рассчитывать уровни;
- проверять бизнес-правила;
- обращаться к Discord API;
- создавать Embed.

Repository работает только с данными.

---

# Discord

Discord Cog максимально тонкий.

Получить Interaction

↓

Создать DTO

↓

Вызвать UseCase

↓

Вернуть ответ

Никакой бизнес-логики внутри Cog.

---

# Web

React работает аналогично Discord.

Получить данные формы

↓

Отправить запрос API

↓

Получить DTO

↓

Отобразить

---

# Конфигурация

Все настройки серверов хранятся в PostgreSQL.

Не использовать config.json для пользовательских настроек.

Конфигурация приложения остается в .env.

---

# Настройки модулей

Каждый модуль самостоятельно хранит свои настройки.

Например

Music

- Enabled
- Volume
- DJ Role
- Autoplay

Relationships

- Enabled
- Max Partners
- Marriage Cost

Economy

- Enabled
- Daily Reward
- Currency Name

Никаких общих таблиц настроек.

---

# EventBus

Модули не вызывают друг друга напрямую.

Используются события.

Пример

MemberJoined

↓

Profile

↓

ProfileCreated

↓

Economy

↓

Relationship

↓

Notification

Таким образом сохраняется независимость.

---

# Redis

Использовать для

- Guild Settings
- Cache
- Sessions
- Frequently Used Data

Не использовать Redis как основную базу данных.

---

# Scheduler

Все фоновые задачи находятся в одном месте.

Например

- Birthday
- Cleanup
- Backup
- AI Memory
- Statistics
- Cache Refresh

Не использовать бесконечные while True.

---

# WebSocket

Предусмотреть поддержку WebSocket.

Использовать для

- обновления очереди музыки;
- отображения текущего трека;
- онлайн-статуса;
- живой статистики.

---

# Dashboard

Главная страница должна содержать

- количество серверов;
- количество пользователей;
- загрузку CPU;
- использование RAM;
- активные модули;
- последние ошибки;
- последние действия.

---

# Страница сервера

Каждый сервер содержит

Overview

Settings

Members

Logs

Modules

Statistics

---

# Модули

Каждый модуль имеет собственный интерфейс.

Например

Music

- настройки;
- очередь;
- история;
- права.

Relationships

- настройки;
- пары;
- предложения;
- статистика.

Economy

- баланс;
- магазин;
- настройки;
- история.

Модули не должны смешиваться.

---

# Logging

Каждая операция логируется.

Лог должен содержать

- Guild ID
- User ID
- Operation
- Duration
- Result

Ошибки всегда содержат стек вызова.

---

# Документация

docs/

    architecture.md

    api.md

    database.md

    deployment.md

    ai_rules.md

    roadmap.md

    events.md

---

# Главные принципы

✅ Один источник бизнес-логики.

✅ Discord и Web используют одинаковые UseCase.

✅ Каждый модуль независим.

✅ Простая замена инфраструктуры.

✅ Минимальная связанность.

✅ Максимальная читаемость.

✅ Предсказуемая структура.

✅ Простое тестирование.

---

# Что запрещено

❌ Discord обращается к Repository напрямую.

❌ Web содержит бизнес-логику.

❌ Feature импортирует другой Feature напрямую.

❌ SQLAlchemy используется вне Infrastructure.

❌ Большие utils.py.

❌ God Objects.

❌ Repository содержит бизнес-логику.

❌ Дублирование UseCase.

---

# Definition of Done

Новая функциональность считается завершенной только если:

□ работает через Discord;

□ работает через REST API;

□ автоматически доступна Web Panel;

□ покрыта тестами;

□ имеет документацию;

□ не нарушает архитектурные правила.

---

# Философия проекта

Этот проект разрабатывается не для быстрого запуска.

Главная цель — создать систему, которая сможет развиваться и поддерживаться годами.

Каждое архитектурное решение должно отвечать на вопрос:

> "Будет ли этот код удобно изменять через пять лет?"

Если ответ отрицательный — решение требует пересмотра.