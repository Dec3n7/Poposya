"""Изолированный сервис рендера карточек: SVG → PNG (librsvg / rsvg-convert).

Вынесен из процесса бота намеренно: рендер карточек не должен делить процесс с
ботом и его секретами. Контейнер сидит на internal-сети без выхода в интернет,
принимает только POST /render с самодостаточным SVG (аватар уже вшит data-URI, а
эмодзи — инлайн Twemoji отправителем) и отдаёт PNG. Никаких секретов внутри.

Раньше движком был headless-Chromium (Playwright, образ 2.15 ГБ, ~4 ядра под
бёрстом). Заменён на librsvg: карточки — SVG, растеризуются лёгким субпроцессом
rsvg-convert (образ ~250 МБ, десятки МБ RAM). См. docs/plans/scale-300-guilds.md
(B1). Контракт /render не изменился: поле `html` теперь несёт SVG.
"""

from __future__ import annotations

import asyncio
import logging
import os

from aiohttp import web
from render_cache import RenderCache, render_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("renderer")

# предохранители на вход: сервис внутренний, но вход всё равно валидируем
_MAX_HTML_BYTES = 4 * 1024 * 1024
_MAX_DIM = 4000
_RSVG_TIMEOUT_SECONDS = 15


def _cache_from_env() -> RenderCache:
    """Границы кэша — из окружения, правятся без пересборки образа. Нули
    (`RENDER_CACHE_MAX_BYTES=0`) полностью выключают кэш — аварийный тумблер."""
    entries = int(os.getenv("RENDER_CACHE_MAX_ENTRIES", "512"))
    mbytes = int(os.getenv("RENDER_CACHE_MAX_BYTES", str(256 * 1024 * 1024)))
    return RenderCache(max_entries=entries, max_bytes=mbytes)


# A3: admission control. С librsvg рендер лёгкий, но кап на одновременные
# субпроцессы rsvg-convert всё равно полезен — не даёт бёрсту расплодить процессы.
# Лишние запросы ждут слот короткое время; не дождались — быстрый 503, по которому
# бот показывает embed-фолбэк (штатный путь при сбое renderer). Cap можно поднять
# выше прежних 3 — субпроцесс дешевле страницы Chromium.
_MAX_CONCURRENCY = int(os.getenv("RENDER_MAX_CONCURRENCY", "4"))
_QUEUE_TIMEOUT = float(os.getenv("RENDER_QUEUE_TIMEOUT_SECONDS", "10"))


class _Renderer:
    """Растеризатор SVG→PNG через rsvg-convert (librsvg). Каждая карточка —
    короткий субпроцесс: SVG на stdin, PNG на stdout. Персистентного процесса нет
    (в отличие от браузера) — старт дешёвый, состояние не копится."""

    async def render(self, svg: str, width: int, height: int, scale: int) -> bytes:
        # -w/-h — размер растра (scale=2 → ретина); SVG сам масштабируется по
        # своему viewBox. Без имён файлов rsvg-convert читает stdin и пишет stdout
        # (важно на read-only rootfs: `-`/`--output -` он бы принял за файл /app/-).
        proc = await asyncio.create_subprocess_exec(
            "rsvg-convert",
            "-w",
            str(width * scale),
            "-h",
            str(height * scale),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(svg.encode("utf-8")), timeout=_RSVG_TIMEOUT_SECONDS
            )
        except TimeoutError:
            proc.kill()
            raise RuntimeError("rsvg-convert timeout") from None
        if proc.returncode != 0 or not out:
            raise RuntimeError(
                f"rsvg-convert упал ({proc.returncode}): {err.decode('utf-8', 'replace')[:200]}"
            )
        return out

    async def close(self) -> None:
        pass  # персистентного процесса нет — закрывать нечего


def create_app(
    renderer: object | None = None,
    cache: RenderCache | None = None,
    max_concurrency: int | None = None,
    queue_timeout: float | None = None,
) -> web.Application:
    # renderer/cache/лимиты инъектируемы: тест подставляет фейк-рендер (без
    # librsvg), свой кэш и малый кап; прод берёт настоящий rsvg-рендер и env.
    renderer = _Renderer() if renderer is None else renderer
    cache = _cache_from_env() if cache is None else cache
    slots = max_concurrency if max_concurrency is not None else _MAX_CONCURRENCY
    timeout = queue_timeout if queue_timeout is not None else _QUEUE_TIMEOUT
    gate = asyncio.Semaphore(slots)

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "cache": cache.stats()})

    async def render(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "bad body"}, status=400)
        html = body.get("html")
        try:
            width = int(body.get("width", 0))
            height = int(body.get("height", 0))
            scale = int(body.get("scale", 2))
        except (TypeError, ValueError):
            return web.json_response({"error": "bad dimensions"}, status=400)
        if not isinstance(html, str) or not html:
            return web.json_response({"error": "html required"}, status=400)
        if not (0 < width <= _MAX_DIM) or not (0 < height <= _MAX_DIM):
            return web.json_response({"error": "dimensions out of range"}, status=400)
        if not (1 <= scale <= 4):
            return web.json_response({"error": "scale out of range"}, status=400)

        # A2: content-addressed кэш. Ключ по SVG+размерам+scale; на попадании
        # отдаём готовый PNG, минуя растеризатор целиком.
        key = render_key(html, width, height, scale)
        cached = cache.get(key)
        if cached is not None:
            # HIT не трогает рендер — слот конкуррентности ему не нужен.
            return web.Response(body=cached, content_type="image/png", headers={"X-Cache": "HIT"})

        # A3: ждём слот ограниченное время; не дождались — 503 (→ embed-фолбэк).
        try:
            await asyncio.wait_for(gate.acquire(), timeout=timeout)
        except TimeoutError:
            return web.json_response({"error": "renderer busy"}, status=503)
        try:
            png = await renderer.render(html, width, height, scale)
        except Exception:
            logger.exception("рендер упал")
            return web.json_response({"error": "render failed"}, status=500)
        finally:
            gate.release()
        cache.put(key, png)
        return web.Response(body=png, content_type="image/png", headers={"X-Cache": "MISS"})

    app = web.Application(client_max_size=_MAX_HTML_BYTES + 64 * 1024)
    app.router.add_get("/health", health)
    app.router.add_post("/render", render)

    async def _cleanup(_: web.Application) -> None:
        close = getattr(renderer, "close", None)
        if close is not None:
            await close()

    app.on_cleanup.append(_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8090)
