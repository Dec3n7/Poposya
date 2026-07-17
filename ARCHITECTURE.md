# Architecture Guide

> Этот документ описывает архитектурные правила проекта.
> Все новые модули и изменения должны соответствовать этим правилам.

---

# Основные принципы

## Цели проекта

Архитектура должна обеспечивать:

- простое добавление новых модулей;
- независимость модулей;
- минимальную связанность;
- простое тестирование;
- возможность заменить инфраструктуру без изменения бизнес-логики;
- долгосрочную поддержку проекта.

Главное правило:

> Код пишется так, чтобы через год его было легко изменить.

---

# Архитектура

Каждая функциональность (Feature) является самостоятельным модулем.

Например:

features/

    profile/

    music/

    moderation/

    relationships/

    economy/

Каждый модуль отвечает только за свою область.

---

# Структура модуля

Каждый модуль содержит четыре слоя.

feature/

    domain/

    application/

    infrastructure/

    presentation/

## Domain

Содержит:

- Entity
- Value Objects
- Domain Services
- Domain Events
- Repository Interfaces

Domain не знает ничего о:

- Discord
- SQLAlchemy
- PostgreSQL
- HTTP
- DI
- Infrastructure

Domain должен быть полностью независимым.

---

## Application

Содержит:

- Use Cases
- DTO
- Interfaces
- бизнес-логику приложения

Application знает только:

- Domain
- Repository Interfaces

Application никогда не знает реализацию Repository.

---

## Infrastructure

Содержит:

- SQLAlchemy
- Discord API
- Redis
- внешние API
- Repository Implementation
- Storage
- Scheduler

Infrastructure зависит от Domain.

Но Domain никогда не зависит от Infrastructure.

---

## Presentation

Содержит:

- Discord Cogs
- Slash Commands
- Buttons
- Views
- Modals

Presentation максимально "тонкий".

Он только:

- получает данные от Discord
- вызывает Use Case
- отправляет ответ

Вся логика находится в Application.

---

# Dependency Rule

Направление зависимостей только одно.

Presentation

↓

Application

↓

Domain

↑

Infrastructure

Никогда наоборот.

---

# Use Cases

Каждое действие пользователя оформляется как отдельный UseCase.

Например

CreateProfileUseCase

PlayMusicUseCase

KickMemberUseCase

GiveRelationshipUseCase

UseCase отвечает только за одну операцию.

---

# Repository

Repository отвечает только за хранение данных.

Repository НЕ содержит:

- бизнес-логику
- проверки
- расчеты

Repository может только:

- save()
- update()
- delete()
- find()

---

# Discord Cog

Cog никогда не содержит бизнес-логику.

Плохой пример:

Cog

↓

SQLAlchemy

↓

Database

Хороший пример:

Cog

↓

UseCase

↓

Repository

---

# DTO

DTO используются для передачи данных между слоями.

DTO никогда не используются как ORM Model.

---

# SQLAlchemy

SQLAlchemy существует только в Infrastructure.

Никакой другой слой не должен импортировать SQLAlchemy.

---

# EventBus

Если одному модулю нужно уведомить другой модуль — используется событие.

Не прямой вызов.

Например

ProfileCreated

↓

Achievement Module

↓

Relationship Module

↓

Notification Module

Таким образом модули остаются независимыми.

---

# Logging

Каждый UseCase обязан писать лог начала и завершения операции.

Ошибки должны содержать:

Guild ID

User ID

Operation

Exception

---

# Исключения

Не использовать Exception.

Создавать собственные ошибки.

Например

UserNotFoundError

TrackNotFoundError

RelationshipAlreadyExistsError

---

# Naming

UseCase

SomethingUseCase

Repository

SomethingRepository

DTO

SomethingDTO

Service

SomethingService

Entity

Something

Интерфейсы

IRepository

IMusicPlayer

IStorage

---

# Что запрещено

❌ Domain импортирует Infrastructure

❌ Cog работает с SQLAlchemy

❌ UseCase знает Discord API

❌ Repository содержит бизнес-логику

❌ Один Feature напрямую изменяет другой Feature

❌ utils.py на тысячи строк

❌ God Objects

❌ Singleton с глобальным состоянием

---

# Когда создавать новый модуль

Если появляется новая предметная область.

Например

Achievements

Economy

AI

Tickets

Voice

Каждый из них получает собственную структуру.

Не добавлять их в существующий модуль.

---

# Когда делать абстракцию

Новая абстракция появляется только если:

- есть минимум два использования;
- ожидается несколько реализаций;
- она действительно уменьшает связанность.

Не создавать интерфейсы "на будущее".

---

# Код

Код должен быть:

простым

предсказуемым

однообразным

лучше написать одинаковую функцию дважды,
чем создать сложную универсальную систему.

---

# Главный вопрос

Перед каждым Pull Request необходимо ответить:

Если удалить этот модуль целиком,

сломаются ли остальные?

Если ответ "нет",

архитектура остается правильной.