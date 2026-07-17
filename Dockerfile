# Stage 1: Builder — только установка зависимостей
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt ./
RUN python -m venv /app/.venv \
    && /app/.venv/bin/pip install --no-cache-dir --upgrade pip \
    && /app/.venv/bin/pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime
FROM python:3.12-slim

# ffmpeg — воспроизведение музыки; libopus0 — кодек голосового канала discord.py;
# postgresql-client — pg_dump для бэкапов Postgres (на trixie это клиент 17,
# он дампит сервер 16; при работе на SQLite просто не используется)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libopus0 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY src/ ./src/
COPY alembic.ini ./

# .env НЕ копируется — конфигурация приходит только снаружи контейнера (ТЗ 13.1):
# env_file в compose, docker run --env-file или секреты оркестратора.

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

CMD ["python", "-m", "src.main"]
