# RUNBOOK — эксплуатация Poposya

Короткие процедуры для прода. Первичная настройка — в [README.md](README.md);
включение HTTPS — в [web/TLS.md](web/TLS.md); чек-лист запуска — заметка
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
2. `docker compose --profile postgres up -d api web` (перезапуск api достаточно).

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
бэкапы. Перед публичным запуском настройте выгрузку наружу, например cron на хосте:

```bash
# ежедневно: копия последних дампов в off-site (S3 / другой хост)
0 4 * * *  docker compose exec -T bot sh -c 'ls -t /app/data/backups/*.dump | head -1' \
           | xargs -I{} docker compose cp bot:{} - | aws s3 cp - s3://ВАШ-БАКЕТ/poposya/$(date +\%F).dump
```

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
2. `pytest -q` локально + пуш → CI (бэкенд + фронт джобы).
3. Коммит с версией в сообщении.

**yt-dlp** ломается на изменениях YouTube чаще остального — держите свежим
(проверяйте ~раз в 1–2 недели или при жалобах на музыку). Симптом: треки не
проигрываются / ошибки извлечения → `yt-dlp` вверх.

Полный lock с хэшами (если захотите жёстче):

```bash
pip install pip-tools
pip-compile --generate-hashes --output-file=requirements.lock requirements.txt
# затем в Dockerfile ставить из requirements.lock
```

---

## 5. Дальнейший хардненинг (опционально)

- **nginx полностью rootless.** Сейчас мастер nginx стартует под root (штатно),
  воркеры — под `nginx`; включён `no-new-privileges`. Для полного rootless —
  базовый образ `nginxinc/nginx-unprivileged:alpine` в `web/Dockerfile`: слушает
  `8080`/`8443` вместо `80`/`443` (поправьте `listen` в `web/nginx.conf`,
  маппинг портов сервиса `web` и, при COPY, `USER root`→`USER nginx`). Даёт
  возможность добавить `cap_drop: [ALL]` и веб-контейнеру.
- **Read-only rootfs.** К bot/api можно добавить `read_only: true` + `tmpfs: [/tmp]`
  (всё писабельное уже на volume `bot_data`); проверьте, что ffmpeg/yt-dlp не
  пишут во временные пути вне `/app/data`.
