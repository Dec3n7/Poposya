# Сборка фронта панели. Stage build — npm install + tsc + vite build (тут же
# ловятся ошибки типов). Stage serve — nginx раздаёт статику с SPA-фолбэком.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --no-fund --no-audit
COPY . .
RUN npm run build

# Rootless: nginx-unprivileged работает под UID 101 (не root), мастер и воркеры.
# Слушает высокие порты (8080/8443), pid и temp-пути — под /tmp, поэтому образ
# совместим с read-only rootfs (tmpfs /tmp в compose). Ни одной capability не
# нужно (cap_drop ALL в compose).
FROM nginxinc/nginx-unprivileged:alpine AS serve
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Заголовки безопасности отдельным файлом: nginx.conf включает его в каждый
# location. Кладём ВНЕ conf.d — иначе штатный `include conf.d/*.conf` в http{}
# подхватил бы снипет вторично на http-уровне.
COPY security-headers.conf /etc/nginx/security-headers.conf
