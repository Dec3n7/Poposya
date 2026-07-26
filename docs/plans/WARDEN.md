# WARDEN

> Autonomous Monitoring & Recovery System for Poposya

---

# Overview

WARDEN — независимый сервис мониторинга, резервного копирования и автоматического восстановления Poposya.

Главная цель WARDEN — обеспечить высокую доступность системы, минимизировать простой и автоматически реагировать на критические ошибки.

WARDEN не является частью Poposya.

Он разворачивается, обновляется и развивается независимо.

---

# Core Principles

## Independence

WARDEN полностью изолирован.

Он не знает внутреннего устройства Poposya.

Допустимые способы взаимодействия:

- Docker API
- HTTP API
- Backup Storage
- Discord Webhook

Запрещено:

- импортировать код Poposya
- использовать внутренние классы
- выполнять бизнес-логику

---

## Reliability First

Любое действие должно иметь минимальные последствия.

Порядок действий:

```
Observe

↓

Verify

↓

Retry

↓

Recover

↓

Notify
```

WARDEN никогда не предпринимает критических действий после одной ошибки.

---

## Universal Design

WARDEN проектируется как самостоятельный продукт.

Poposya является первым поддерживаемым сервисом.

В будущем WARDEN должен иметь возможность обслуживать любые контейнеризированные приложения.

---

# Architecture

```
                    Web Panel
                         │
                   HTTPS / REST
                         │
                ┌────────▼────────┐
                │     WARDEN      │
                │─────────────────│
                │ Monitoring      │
                │ Recovery        │
                │ Backups         │
                │ Incident Log    │
                │ Health Engine   │
                └────────┬────────┘
                         │
                 Docker Engine API
                         │
         ┌───────────────┴───────────────┐
         │                               │
    Poposya Container              PostgreSQL
```

WARDEN является единственной системой, имеющей право управлять контейнерами.

Веб-панель никогда не работает напрямую с Docker.

---

# Responsibilities

WARDEN отвечает за:

- мониторинг
- резервное копирование
- восстановление
- логирование инцидентов
- сбор статистики
- уведомления
- управление состоянием системы

WARDEN не отвечает за:

- Discord
- AI
- Cog
- бизнес-логику
- миграции БД

---

# Health Engine

Источник состояния:

```
GET /health
```

Пример ответа

```json
{
    "status": "READY",
    "database": true,
    "discord": true,
    "uptime": 36000,
    "version": "2.0.0"
}
```

Дополнительно проверяется

- состояние контейнера
- время ответа API
- доступность Docker
- доступность БД

---

# System States

```
UNKNOWN
STARTING
READY
DEGRADED
MAINTENANCE
SHUTTING_DOWN
FAILED
RESTORING
```

Описание:

| State | Назначение |
|--------|------------|
| STARTING | сервис запускается |
| READY | полностью работоспособен |
| DEGRADED | работает частично |
| MAINTENANCE | обслуживание, мониторинг приостановлен |
| FAILED | критическая ошибка |
| RESTORING | выполняется восстановление |

---

# Recovery Pipeline

Stage 1

Повторный Health Check

↓

Stage 2

Повторная проверка через небольшой интервал

↓

Stage 3

Soft Restart

↓

Stage 4

Exponential Backoff

↓

Stage 5

Restore Backup

↓

Stage 6

Notification

---

# Backup System

Поддерживается:

- ручное создание
- автоматическое создание
- восстановление

Интервал

```
30–60 минут
```

Сохраняются:

- PostgreSQL
- пользовательские данные
- конфигурация
- локальные файлы

---

# Incident Manager

Каждый инцидент сохраняется.

Пример

```json
{
    "id": 14,
    "started": "...",
    "reason": "Health timeout",
    "restarts": 2,
    "backup": "backup_2026_07_22",
    "duration": 48,
    "status": "Recovered"
}
```

---

# Monitoring API

WARDEN предоставляет собственный REST API.

## Status

```
GET /status
```

Возвращает текущее состояние WARDEN.

---

## Health

```
GET /health
```

Проверка состояния самого WARDEN.

---

## Metrics

```
GET /metrics
```

Статистика.

---

## Incidents

```
GET /incidents
```

История инцидентов.

---

## Restart

```
POST /restart
```

Мягкий рестарт.

---

## Backup

```
POST /backup
```

Создать резервную копию.

---

## Restore

```
POST /restore
```

Восстановить резервную копию.

---

## Maintenance

```
POST /maintenance
```

Перевести систему в режим обслуживания.

Во время обслуживания запрещено:

- выполнять restart
- выполнять restore
- выполнять recovery

Мониторинг продолжается только в режиме наблюдения.

---

## Resume

```
POST /resume
```

Выход из режима обслуживания.

---

# Configuration

Основная конфигурация хранится отдельно.

Например

```
config.toml
```

или

```
config.yaml
```

Содержит:

- интервалы проверок
- настройки Backup
- Recovery Policy
- контейнеры
- Health Endpoint

Секреты размещаются только в

```
.env
```

Например:

- Webhook
- API Keys
- Tokens

---

# Health Score

WARDEN рассчитывает общий показатель здоровья системы.

Максимум

```
100
```

Например

| Проверка | Баллы |
|----------|-------|
| Container | 25 |
| API | 25 |
| PostgreSQL | 20 |
| Discord | 15 |
| Docker | 10 |
| Response Time | 5 |

Пример

```
Health Score

94 / 100
```

Используется для диагностики и отображается в Web Panel.

---

# Web Panel Integration

Веб-панель взаимодействует исключительно с API WARDEN.

Отображается:

- состояние WARDEN
- состояние Poposya
- Health Score
- статус Backup
- история инцидентов
- последние ошибки
- uptime
- время последнего Backup
- активный режим (READY / MAINTENANCE / RESTORING)

Поддерживаемые действия:

- Restart
- Backup Now
- Restore
- Maintenance Mode
- Resume Monitoring

---

# Dashboard

Планируемые виджеты

```
 WARDEN

Running
```

```
Poposya

Healthy
```

```
Health Score

98 / 100
```

```
Last Backup

18 minutes ago
```

```
Incidents

0
```

```
Uptime

12d 04h
```

---

# Logging

Каждое действие журналируется.

Пример

```
[INFO] Monitoring started

[INFO] Health OK

[WARNING] Health timeout

[WARNING] Restart attempt #1

[ERROR] Recovery started

[INFO] Recovery completed
```

---

# Notifications

Поддерживаемые уровни:

```
INFO

WARNING

ERROR

CRITICAL
```

Поддерживаемые каналы:

- Discord Webhook

В будущем:

- Telegram
- Email

---

# Future

Планируется:

- Prometheus Exporter
- Grafana Dashboard
- Self Diagnostics
- Cluster Support
- Multiple Containers
- Backup Compression
- Backup Encryption
- Scheduled Cleanup
- Role-based API Access
- WebSocket для Live Dashboard

---

# Design Goals

WARDEN должен быть:

- независимым
- расширяемым
- отказоустойчивым
- предсказуемым
- универсальным

