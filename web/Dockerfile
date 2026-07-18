# Сборка фронта панели. Stage build — npm install + tsc + vite build (тут же
# ловятся ошибки типов). Stage serve — nginx раздаёт статику с SPA-фолбэком.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --no-fund --no-audit
COPY . .
RUN npm run build

FROM nginx:alpine AS serve
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
