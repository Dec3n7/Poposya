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
# он дампит сервер 16; при работе на SQLite просто не используется);
# fonts-dejavu-core — TTF с кириллицей для карточки /rank (Pillow ищет DejaVuSans)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg libopus0 postgresql-client fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY src/ ./src/
COPY alembic.ini ./

# .env НЕ копируется — конфигурация приходит только снаружи контейнера (ТЗ 13.1):
# env_file в compose, docker run --env-file или секреты оркестратора.

# Непривилегированный пользователь: побег из контейнера не даёт root на хосте.
# /app/data — рабочий каталог (SQLite/логи/бэкапы/аудиокэш), должен быть писан
# этим пользователем. Фиксированный UID 10001 — чтобы права на volume были
# предсказуемы (см. RUNBOOK: одноразовый chown при апгрейде со старого root-образа).
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home app \
    && mkdir -p /app/data \
    && chown -R app:app /app
USER app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["python", "-m", "src.main"]
