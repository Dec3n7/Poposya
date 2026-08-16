# RUNBOOK — эксплуатация Poposya

Короткие процедуры для прода. Первичная настройка — в [README.md](../README.md);
включение HTTPS — в [web/TLS.md](../web/TLS.md); чек-лист запуска — заметка
`public-launch-checklist`.

---

## 1. Апгрейд на non-root образ: права на volume `bot_data`

Начиная с хардненинга 2026-07-26 контейнеры бота/api работают под
непривилегированным пользователем **UID 10001**. Каталог `/app/data` (SQLite,
логи, бэкапы, аудиокэш) должен принадлежать ему.

- **Свежий volume** — Docker сам инициализирует его правами из образа, действий не нужно.
- **Существующий `bot_data` со старого root-образа** — один раз почините права,
  иначе бот упадёт на записи:

```bash
docker compose run --rm --user root --entrypoint sh bot -c "chown -R 10001:10001 /app/data"
```

Альтернатива на Postgres-профиле: в `bot_data` лежат только регенерируемые данные
(логи/аудиокэш) и дампы. Дампы при желании сохраните наружу (см. §3) и
пересоздайте volume: `docker compose down && docker volume rm <проект>_bot_data`.

Проверка после старта: `docker compose exec bot id` → `uid=10001`.

---

## 2. Аварийный logout всех сессий панели

Токен сессии — подписанный JWT со сроком до `WEB_SESSION_TTL_HOURS` (24 ч). Стора
сессий на сервере нет, поэтому «выкинуть всех» (утёкший токен, разжалованный
админ, инцидент) — это **бамп версии**:

1. В `.env`: увеличьте `WEB_SESSION_VERSION` на 1 (было `1` → станет `2`).
2. `docker compose up -d api web` (перезапуск api достаточно).

Все ранее выданные токены мгновенно недействительны — сверка claim `sv` в
`decode_session` (`src/api/security.py`). Ротация секрета подписи при этом не
нужна. Помните: права гильдий вшиты в токен на момент логина, так что бамп версии
— это и способ форсировать переполучение прав после смены ролей в Discord.

---

## 3. Бэкапы Postgres и вынос наружу

Бот сам делает `pg_dump -Fc` при старте и раз в `BACKUP_INTERVAL_HOURS` (по умолч.
24 ч), хранит последние `BACKUP_KEEP` (7) в `BACKUP_DIR` (= `/app/data/backups` на
volume `bot_data`). Код — `src/infrastructure/db/backup.py`.

⚠️ Дампы лежат на **том же хосте**, что и БД: падение диска/хоста уносит и базу, и
бэкапы. Перед публичным запуском настройте выгрузку наружу — двумя способами:

**In-app** (встроено): задайте `BACKUP_OFFSITE_CMD` в `.env` — бот прогонит каждый
свежий дамп этой командой (`{path}` = путь дампа), напр.
`rclone copy {path} <remote>:poposya-backups/`. Пусто = выкл; провал выгрузки не
фатален. Нужно положить инструмент выгрузки и креды в контейнер бота.

**Host-cron** (не трогает образ бота):
```bash
VOL=$(docker volume inspect poposyap_bot_data -f '{{.Mountpoint}}')
0 4 * * *  rclone copy $VOL/backups <remote>:poposya-backups/ --min-age 1m
```

Полная процедура развёртывания с бэкапом и мониторингом — [DEPLOY.md](DEPLOY.md).

Ручной дамп/восстановление:

```bash
docker compose exec db pg_dump -U poposya -Fc poposya > poposya_$(date +%F).dump
# восстановление в ЧИСТУЮ базу:
docker compose exec -T db pg_restore -U poposya -d poposya --clean --if-exists < poposya_ГГГГ-ММ-ДД.dump
```

Проверяйте восстановимость дампа хотя бы раз перед запуском — непроверенный бэкап
бэкапом не считается.

---

## 4. Обновление зависимостей

`requirements.txt` закреплён точно (`==`) ради воспроизводимых сборок. Обновление —
осознанный шаг, не самотёком:

1. Поднимите версию в `requirements.txt` (или `pip install -U <пакет>` в venv и
   впишите новую версию).
2. **Перегенерируйте `requirements.lock`** (ОБЯЗАТЕЛЬНО — иначе Docker-образ
   соберётся со старыми версиями, см. ниже).
3. `pytest -q` локально + пуш → CI (бэкенд + фронт джобы).
4. Коммит с версией в сообщении (и `requirements.txt`, и `requirements.lock`).

**yt-dlp** ломается на изменениях YouTube чаще остального — держите свежим
(проверяйте ~раз в 1–2 недели или при жалобах на музыку). Симптом: треки не
проигрываются / ошибки извлечения → `yt-dlp` вверх (не забудьте перелочить).

### Регенерация requirements.lock (supply-chain: хэши)

`requirements.txt` — человекочитаемый ВХОД; `requirements.lock` — производный
lock с хэшами ВСЕХ зависимостей, из него Docker-образ ставит через
`--require-hashes`. Хэши **платформозависимы**, поэтому лок генерируется в том же
образе, что и сборка (`python:3.12-slim`):

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  "pip install pip-tools && pip-compile --generate-hashes -o requirements.lock requirements.txt"
```

Проверка, что лок не устарел: пересоберите образ (`docker compose build bot`) —
`--require-hashes` упадёт, если версия в локе не совпала с реально доступной.

---

## 5. Включение токена `/health/full` (порядок важен)

`/health/full` светит внутренности (cogs, БД, outbox, ошибки). Его можно закрыть
токеном (`HEALTH_FULL_TOKEN` у бота, `WARDEN_HEALTH_TOKEN` у сторожа — одно
значение). `/health` и `/ready` остаются открытыми (docker healthcheck).

**Опасность неверного порядка:** если бот начнёт ТРЕБОВАТЬ токен раньше, чем
WARDEN начнёт его СЛАТЬ, зонд получит 401 → score 0 → ложный CRITICAL и, в
armed-режиме, рестарт бота. Поэтому строго:

```bash
# 0. (опц.) пауза сторожа, чтобы точно не было рестарт-войны
#    из панели/Discord: /warden pause 15

# 1. WARDEN получает секрет ПЕРВЫМ и начинает его слать (бот пока открыт — ок)
#    в WARDEN/.env: WARDEN_HEALTH_TOKEN=<секрет>
cd WARDEN && docker compose up -d warden

# 2. Тем же значением закрываем бота
#    в Poposya P/.env: HEALTH_FULL_TOKEN=<тот же секрет>
cd "../Poposya P" && docker compose up -d bot

# 3. Проверка
curl -H "X-Health-Token: <секрет>" http://127.0.0.1:8080/health/full   # 200
curl http://127.0.0.1:8080/health/full                                  # 401
curl http://127.0.0.1:8080/health                                       # 200

# 4. (опц.) снять паузу: /warden resume
```

Откат: очистить `HEALTH_FULL_TOKEN` у бота и перезапустить `bot` — эндпоинт снова
открыт; лишний заголовок от WARDEN безвреден.

---

## 6. Хардненинг контейнеров (сделано) и проверка после деплоя

Все четыре сервиса (`bot`, `api`, `web`, `renderer`) идут с `cap_drop: [ALL]`,
`no-new-privileges` и `read_only: true` (писчее — в `tmpfs`/на volume).

- **web/nginx — rootless (UID 101).** Образ `nginxinc/nginx-unprivileged:alpine`,
  слушает `8080`/`8443` (не `80`/`443`), pid и temp — под `/tmp` (tmpfs). Capability
  не требуется (`cap_drop: [ALL]`, без `cap_add`). **Проверка после деплоя:**
  1. `docker compose up -d --build web` — контейнер поднялся и `healthy`
     (`docker compose ps`; healthcheck бьёт внутренний `http://127.0.0.1:8080/`).
  2. Панель открывается на `https://localhost:8443`, логин и мутации (напр.
     сохранить настройку) проходят.
  3. **Права на сертификат:** приватный ключ `web/certs/privkey.pem` должен быть
     ЧИТАЕМ пользователем `nginx` (UID 101) — не `0600 root`. Иначе nginx не
     стартует (`SSL_CTX_use_PrivateKey ... permission denied` в логах web).
     Проверить: `docker compose logs web | grep -i ssl`.

## 7. Ревалидация прав Discord в панели (`WEB_PERM_TTL_MINUTES`)

Права Discord вшиваются в сессию при входе (OAuth-токен не хранится). Чтобы
разжалованный в Discord админ не сохранял доступ к ДЕЙСТВИЯМ панели весь TTL
сессии, задайте `WEB_PERM_TTL_MINUTES` (напр. `30`): при снимке прав старше
этого привилегированные действия (бан/кик/тайм-аут/роли/настройки) вернут `401`
«права устарели — войдите заново», и пользователь перелогинится со свежими
правами. Чтения и операторские права (`WEB_OPERATOR_IDS`) не затрагиваются.
`0` (по умолчанию) — выключено (граница только по TTL/idle сессии).
