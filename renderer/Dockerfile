# Изолированный сервис рендера карточек (HTML → PNG через Chromium).
# Отдельный контейнер: браузер под --no-sandbox не делит процесс с ботом, не
# видит Discord-токен/БД и сидит на internal-сети без интернета (см. compose).

# Stage 1: builder — зависимости из lock с хэшами (--require-hashes: supply-chain)
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.lock ./
RUN python -m venv /app/.venv \
    && /app/.venv/bin/pip install --no-cache-dir --upgrade pip \
    && /app/.venv/bin/pip install --no-cache-dir --require-hashes -r requirements.lock

# Stage 2: runtime
FROM python:3.12-slim

# шрифты для Chromium-рендера: кириллический TTF (иначе «квадратики») + цветные
# эмодзи для эмблем ачивок. Перенесены из образа бота — теперь браузер только тут.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-dejavu-core fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app/.venv ./.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install-deps chromium

COPY server.py ./

# непривилегированный пользователь (UID отличается от бота — 10002)
RUN groupadd --system --gid 10002 renderer \
    && useradd --system --uid 10002 --gid renderer --home-dir /app --no-create-home renderer \
    && mkdir -p /ms-playwright \
    && chown -R renderer:renderer /app /ms-playwright
USER renderer

# браузер качаем под renderer в общий каталог (как у бота)
RUN playwright install chromium

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8090
CMD ["python", "server.py"]
