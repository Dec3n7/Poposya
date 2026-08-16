# Изолированный сервис рендера карточек: SVG → PNG через librsvg (rsvg-convert).
# Раньше был headless-Chromium (Playwright, образ ~2.15 ГБ, ~4 ядра под бёрстом).
# librsvg — лёгкий растеризатор SVG: образ ~250 МБ, десятки МБ RAM. Карточки-SVG
# строит бот (эмодзи вшиты инлайн-Twemoji) и шлёт сюда — здесь только rsvg-convert.
FROM python:3.12-slim

# librsvg (rsvg-convert) + шрифты: кириллица/латиница (DejaVu). Эмодзи-шрифт НЕ
# нужен — эмодзи приходят инлайн-SVG. fc-cache строит кэш шрифтов в образ, чтобы
# fontconfig не писал его в рантайме (совместимо с read-only rootfs).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        librsvg2-bin fonts-dejavu-core fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# непривилегированный пользователь (UID отличается от бота — 10002)
RUN groupadd --system --gid 10002 renderer \
    && useradd --system --uid 10002 --gid renderer --home-dir /app --no-create-home renderer
USER renderer

# HOME в tmpfs: если библиотека вдруг захочет кэш — пишет в /tmp, а не в ro-/app
ENV HOME=/tmp \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1

# код — последним слоём: правки не инвалидируют apt/pip выше
COPY server.py render_cache.py ./

EXPOSE 8090
CMD ["python", "server.py"]
