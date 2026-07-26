# TLS для панели (публичный запуск)

Сейчас nginx слушает только `:80`, наружу проброшен как `8081:80`. Для публики
нужен HTTPS. Куки сессии станут `Secure` автоматически, как только
`web_oauth_redirect` в `.env` начнётся с `https` (см. `_secure_cookies` в
`src/api/routers/auth.py`) — но сам TLS надо поднять одним из двух способов.

Всё нужное на стороне nginx уже готово: заголовки безопасности (включая HSTS,
который активируется сам поверх https) и закомментированный блок `server {
listen 443 ssl }` в `web/nginx.conf`.

## Вариант A — TLS терминирует nginx панели (самодостаточно)

1. Получите сертификат на домен (Let's Encrypt / certbot в режиме webroot или
   DNS-01). Положите `fullchain.pem` и `privkey.pem` в каталог, который
   смонтируете в контейнер `web` как `/etc/nginx/certs`.
2. В `docker-compose.yml` у сервиса `web`:
   - опубликуйте 443: добавьте `"443:443"` в `ports` (можно оставить и `8081:80`
     для внутренних проверок или убрать);
   - смонтируйте сертификаты: `volumes: ["./certs:/etc/nginx/certs:ro"]`.
3. В `web/nginx.conf` раскомментируйте блок `server { listen 443 ssl }`, впишите
   `server_name`. Затем в блоке `server { listen 80 }` замените отдачу статики на
   редирект на https:
   ```nginx
   location / { return 301 https://$host$request_uri; }
   ```
   (роут `/.well-known/acme-challenge/` оставьте на :80, если продлеваете
   сертификат через webroot).
4. В `.env`: `WEB_ALLOWED_ORIGIN=https://ВАШ_ДОМЕН` и
   `web_oauth_redirect=https://ВАШ_ДОМЕН/api/auth/callback`. Тот же
   `https://…/api/auth/callback` добавьте в Discord Developer Portal → OAuth2 →
   Redirects.
5. `docker compose --profile postgres up -d --build`.

## Вариант B — TLS терминирует прокси впереди (Caddy / Traefik / Cloudflare)

Если HTTPS даёт внешний реверс-прокси и на nginx приходит уже http:

1. Блок 443 в `web/nginx.conf` не трогаете (остаётся http на :80), прокси
   форвардит на `web:80` (или проброшенный порт хоста).
2. Чтобы rate-limit и логи видели настоящий IP клиента, а не прокси, в
   `web/nginx.conf` раскомментируйте и сузьте до сети прокси:
   ```nginx
   set_real_ip_from <подсеть_прокси>;
   real_ip_header X-Forwarded-For;
   ```
3. `.env` и Discord-redirect — как в варианте A (публичный https-URL).
4. Прокси должен передавать `X-Forwarded-Proto: https` — тогда HSTS и Secure-куки
   отработают корректно.

## Проверка после включения

- `https://домен` открывается, замок валиден, http редиректит на https.
- В браузере (DevTools → Network → любой ответ) присутствуют заголовки
  `content-security-policy`, `strict-transport-security`, `x-frame-options`.
- Вход через Discord доходит до панели, кука `poposya_session` помечена `Secure`.
- В консоли браузера нет ошибок CSP (если появятся из-за нового внешнего ресурса
  — допишите его источник в `web/security-headers.conf`).
