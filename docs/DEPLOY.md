# DEPLOY — развёртывание Poposya на свежем сервере (turnkey)

> Пошаговый запуск всего стека на чистом VPS (Ubuntu 22.04 / Debian 12, KVM,
> root или sudo). Цель — довести до рабочего бота + панели на своём домене с
> HTTPS, мониторингом и off-box бэкапом. Ongoing-эксплуатация — в [RUNBOOK.md](RUNBOOK.md).
>
> Плейсхолдеры по тексту: `<IP>` — адрес сервера, `<DOMAIN>` — домен панели
> (напр. `panel.example.com`), `<EMAIL>` — почта для Let's Encrypt.

## 0. Что нужно на руках

- VPS с root/sudo и SSH (рекомендация под 300 гильдий — 4 vCPU / 8 ГБ / NVMe, см.
  [perf-baseline](plans/perf-baseline.md)).
- Домен и доступ к его DNS.
- Discord: **Bot Token** и приложение в Developer Portal (для OAuth панели).
- Заполненный `.env` (по [.env.example](../.env.example)) — можно готовить локально.

---

## 1. SSH-ключ вместо пароля

На **своей** машине:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/poposya -C poposya-deploy
ssh-copy-id -i ~/.ssh/poposya.pub root@<IP>     # один раз спросит пароль сервера
ssh -i ~/.ssh/poposya root@<IP>                  # дальше — по ключу
```

Для удобства в `~/.ssh/config`:
```
Host poposya
  HostName <IP>
  User root
  IdentityFile ~/.ssh/poposya
```

## 2. Базовый hardening (на сервере)

```bash
apt update && apt -y upgrade
apt -y install ufw fail2ban unattended-upgrades
dpkg-reconfigure -plow unattended-upgrades      # автообновления безопасности

# firewall: только SSH, HTTP (для Let's Encrypt) и HTTPS (панель)
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# SSH только по ключу
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh
```

> ⚠️ **Docker и ufw.** Docker публикует порты контейнеров напрямую через iptables,
> в обход ufw. У нас это не дыра: наружу публикуется **только панель** (443), а
> health бота (`8080`) прибит к `127.0.0.1`, `db`/`renderer` не публикуются вовсе.
> ufw здесь защищает хостовые сервисы (SSH) и явно фиксирует намерение.

## 3. Docker + Compose

```bash
curl -fsSL https://get.docker.com | sh
docker --version && docker compose version    # v2 (плагин compose) обязателен
```

## 4. Клонирование стека

```bash
cd /opt
git clone https://github.com/Dec3n7/Poposya.git poposya
cd /opt/poposya
```

## 5. Секреты и `.env`

```bash
cp .env.example .env
nano .env
```

Обязательный минимум в `.env` (compose падёт без первых двух):
```dotenv
POSTGRES_PASSWORD=<надёжный-пароль>
BOT_DATABASE_URL=postgresql+asyncpg://poposya:<тот-же-пароль>@db:5432/poposya
DISCORD_TOKEN=<токен-бота>
WEB_ALLOWED_ORIGIN=https://<DOMAIN>
WEB_OAUTH_REDIRECT=https://<DOMAIN>/api/auth/callback
```

Опционально: `YTDLP_COOKIES_FILE` (положив файл в `./secrets/`), `BACKUP_OFFSITE_CMD`
(см. шаг 10), токен `/health/full` (см. [RUNBOOK §5](RUNBOOK.md)).

`./secrets/` монтируется в бота read-only — туда кладут cookies YouTube, если нужны.

## 6. Домен → DNS

У регистратора добавьте **A-запись** `<DOMAIN>` → `<IP>`. Дождитесь распространения:
```bash
dig +short <DOMAIN>     # должен вернуть <IP>
```

## 7. HTTPS (Let's Encrypt)

nginx панели уже слушает `8443 ssl` и читает сертификаты из `/etc/nginx/certs/`
(`fullchain.pem` + `privkey.pem`) — по умолчанию там самоподписанные. Меняем на
настоящие:

```bash
apt -y install certbot
# порт 80 сейчас свободен (панель на 443) → standalone-челлендж проходит
certbot certonly --standalone -d <DOMAIN> --agree-tos -m <EMAIL> --no-eff-email

# подкладываем сертификаты туда, откуда их монтирует контейнер web
cp /etc/letsencrypt/live/<DOMAIN>/fullchain.pem /opt/poposya/web/certs/fullchain.pem
cp /etc/letsencrypt/live/<DOMAIN>/privkey.pem   /opt/poposya/web/certs/privkey.pem
# ключ должен читаться пользователем nginx в контейнере (UID 101), не root-only
chown 101:101 /opt/poposya/web/certs/fullchain.pem /opt/poposya/web/certs/privkey.pem
chmod 600 /opt/poposya/web/certs/privkey.pem
```

Опубликуйте панель на 443: в `docker-compose.yml` у сервиса `web` замените
`"8443:8443"` на `"443:8443"`.

**Discord OAuth:** в Developer Portal → приложение → OAuth2 → Redirects добавьте
`https://<DOMAIN>/api/auth/callback` (тот же, что `WEB_OAUTH_REDIRECT`). Без этого
логин в панель не пройдёт.

**Автопродление** (сертификат живёт 90 дней) — deploy-hook перекладывает и
перезапускает web:
```bash
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat >/etc/letsencrypt/renewal-hooks/deploy/poposya.sh <<'HOOK'
#!/bin/sh
D=<DOMAIN>
cp /etc/letsencrypt/live/$D/fullchain.pem /opt/poposya/web/certs/fullchain.pem
cp /etc/letsencrypt/live/$D/privkey.pem   /opt/poposya/web/certs/privkey.pem
chown 101:101 /opt/poposya/web/certs/*.pem
docker compose -f /opt/poposya/docker-compose.yml restart web
HOOK
chmod +x /etc/letsencrypt/renewal-hooks/deploy/poposya.sh
certbot renew --dry-run     # проверка; systemd-таймер certbot ставится сам
```

## 8. Запуск стека

Тяжёлый образ renderer собираем отдельно первым — так параллельная сборка не
конкурирует за сеть/CPU (см. [scale-300-guilds](plans/scale-300-guilds.md)):

```bash
cd /opt/poposya
docker compose build renderer
docker compose up -d --build
docker compose ps           # ждём все healthy (db → api/bot → web)
```

Проверка:
```bash
# панель отвечает (изнутри; снаружи — https://<DOMAIN> в браузере)
curl -sk https://localhost:443/ -o /dev/null -w '%{http_code}\n'   # 200
# кэш рендера: MISS → HIT
docker exec poposyap-bot-1 python -c "import urllib.request,json;\
b=json.dumps({'html':'<b>ok</b>','width':60,'height':30,'scale':2}).encode();\
[print(urllib.request.urlopen(urllib.request.Request('http://renderer:8090/render',data=b,headers={'Content-Type':'application/json'}),timeout=25).headers.get('X-Cache')) for _ in (1,2)]"
```

## 9. WARDEN (сторож — отдельный compose-проект)

WARDEN живёт в своём репозитории и **отдельном** compose-проекте (чтобы деплой
Poposya не перезапускал сторожа). Он цепляется к сети стека `poposyap_default`
(в его compose она `external: true`), поэтому поднимать его **после** шага 8.

```bash
cd /opt
git clone https://github.com/Dec3n7/WARDEN.git warden
cd /opt/warden
cp .env.example .env && nano .env      # токен бота-уведомителя, ID владельца
docker compose up -d --build
docker logs warden | tail              # ждём: цели READY
```

> Если включаете токен `/health/full`, порядок «WARDEN получает секрет ПЕРВЫМ» —
> в [RUNBOOK §5](RUNBOOK.md).

## 10. Off-box бэкап

Локальные `pg_dump` уже идут автоматически в volume `bot_data` (см. `BACKUP_*` в
`.env`). Но они на **том же боксе**, что и БД — нужна копия наружу. Два пути:

**A. Host-cron + rclone (рекомендуется — не трогает хардненный образ бота):**
```bash
apt -y install rclone
rclone config                          # настроить remote: S3/Backblaze/другой хост
VOL=$(docker volume inspect poposyap_bot_data -f '{{.Mountpoint}}')
# ежедневно в 04:00 копировать свежие дампы наружу
( crontab -l 2>/dev/null; echo "0 4 * * * rclone copy $VOL/backups <remote>:poposya-backups/ --min-age 1m" ) | crontab -
```

**B. In-app (`BACKUP_OFFSITE_CMD`):** бот сам гонит каждый свежий дамп наружу.
Задать в `.env` (пример: `BACKUP_OFFSITE_CMD=rclone copy {path} <remote>:poposya-backups/`)
и пробросить в контейнер бота бинарь rclone и его конфиг (volume, ro). Удобно,
когда хочется выгрузку сразу после дампа, а не по расписанию.

Восстановление из дампа — [RUNBOOK §3](RUNBOOK.md) (`pg_restore` в чистую базу).

## 11. Внешний аптайм-монитор

WARDEN крутится на том же боксе и **не поймает падение самого хоста**. Заведите
внешний монитор (UptimeRobot / BetterStack / Healthchecks — бесплатные тарифы):
HTTPS-проверка `https://<DOMAIN>/` каждые 1–5 мин. Через пару недель — своя
статистика аптайма, которой у хостера нет.

---

## Обновление / редеплой

```bash
cd /opt/poposya
git pull
docker compose up -d --build      # пересоберёт изменённое, пересоздаст контейнеры
docker compose ps
```

WARDEN обновляется независимо: `cd /opt/warden && git pull && docker compose up -d --build`.

## Чеклист после деплоя

- [ ] `docker compose ps` — все 5 сервисов `healthy`.
- [ ] `https://<DOMAIN>` открывается, сертификат валиден (не self-signed).
- [ ] Логин в панель через Discord проходит (OAuth redirect совпадает).
- [ ] Кэш рендера: `X-Cache` даёт `MISS` затем `HIT`.
- [ ] WARDEN: все цели `READY` (включая web).
- [ ] Бот онлайн в Discord; музыка играет без заиканий; `/rank` рисует карточку.
- [ ] Через сутки: локальный дамп создан **и** копия улетела в off-box remote.
- [ ] Внешний аптайм-монитор зелёный.

## Откат

```bash
cd /opt/poposya
git log --oneline -5              # найти предыдущий рабочий коммит
git checkout <commit>
docker compose up -d --build
```
Порча данных БД — восстановление из дампа по [RUNBOOK §3](RUNBOOK.md).

## Частые грабли

- **Панель не логинит:** `WEB_OAUTH_REDIRECT`, `WEB_ALLOWED_ORIGIN` и Redirect в
  Discord Developer Portal должны совпадать буква-в-букву (схема, домен, путь).
- **nginx не стартует после подмены серта:** приватный ключ не читается под UID
  101 — проверьте `chown 101:101 web/certs/*.pem`.
- **web как CRITICAL в WARDEN:** сторож должен стучать во внутренний `:8080`
  контейнера web, не в `:80` (nginx rootless слушает высокие порты).
- **renderer «не поднялся» при сборке всех разом:** соберите его первым
  (`docker compose build renderer`), затем `up` — см. scale-300-guilds.
```
