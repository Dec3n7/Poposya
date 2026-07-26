import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from aiohttp import web

logger = logging.getLogger(__name__)

HealthCheck = Callable[[], Awaitable[bool]]
# Провайдер метрик отдаёт сырые числа без интерпретации: решение о том, что
# считать нормой, принимает WARDEN, а не бот (см. docs/plans/WARDEN V2.md).
MetricProvider = Callable[[], Awaitable[dict[str, Any]]]

# на сколько усыпляем луп, замеряя его загруженность
_LAG_PROBE_SECONDS = 0.05


class HealthChecker:
    """Реестр проверок здоровья (ТЗ 9.3). Упавшая проверка — это False,
    а не исключение наружу.

    Две независимые части:

    * `checks` — булевы ворота «жив / мёртв», на них завязан Docker healthcheck;
    * `metrics` — сырые числа для внешнего мониторинга (насколько хорошо жив).

    Ворота намеренно остались булевыми: их читает `/health`, а его контракт
    менять нельзя, не сломав healthcheck в docker-compose.
    """

    def __init__(self) -> None:
        self.checks: dict[str, HealthCheck] = {}
        self.metrics: dict[str, MetricProvider] = {}
        self._started_at = time.monotonic()

    def register(self, name: str, check: HealthCheck) -> None:
        self.checks[name] = check

    def register_metric(self, name: str, provider: MetricProvider) -> None:
        self.metrics[name] = provider

    async def check(self) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for name, check_fn in self.checks.items():
            try:
                results[name] = bool(await check_fn())
            except Exception:
                logger.warning("Health-проверка упала", extra={"check": name}, exc_info=True)
                results[name] = False
        return results

    async def collect(self) -> dict[str, Any]:
        """Сырые метрики по группам. Сбойный провайдер не роняет остальные и не
        исчезает из ответа: вместо чисел приходит {"error": …}, иначе мониторинг
        не отличит «метрики нет» от «метрика равна нулю»."""
        results: dict[str, Any] = {}
        for name, provider in self.metrics.items():
            try:
                results[name] = await provider()
            except Exception as exc:
                logger.warning("Сбор метрики упал", extra={"metric": name}, exc_info=True)
                results[name] = {"error": f"{type(exc).__name__}: {exc}"}
        return results

    def uptime_seconds(self) -> float:
        return round(time.monotonic() - self._started_at, 1)


async def measure_event_loop_lag() -> float:
    """Задержка планировщика в миллисекундах: насколько сон затянулся сверх
    запрошенного. Растёт раньше любого другого симптома, когда луп забит
    синхронной работой, и первым же ловит «процесс жив, но не отвечает»."""
    loop = asyncio.get_running_loop()
    start = loop.time()
    await asyncio.sleep(_LAG_PROBE_SECONDS)
    overshoot = (loop.time() - start - _LAG_PROBE_SECONDS) * 1000
    # на Windows гранулярность таймера (~15 мс) даёт сон чуть короче заказанного
    # и, как следствие, отрицательную задержку; для мониторинга это шум, а не
    # сигнал — «луп свободен» и «луп свободен с запасом» одинаково означают ноль
    return round(max(overshoot, 0.0), 2)


def create_web_app(health_checker: HealthChecker) -> web.Application:
    app = web.Application()

    async def health_handler(request: web.Request) -> web.Response:
        status = await health_checker.check()
        if all(status.values()):
            return web.json_response({"status": "healthy", "checks": status})
        return web.json_response({"status": "unhealthy", "checks": status}, status=503)

    async def health_full_handler(request: web.Request) -> web.Response:
        """Полная картина для WARDEN: ворота + сырые метрики.

        Всегда 200, даже когда ворота закрыты. Это не liveness-проба, а источник
        данных: на 503 промежуточные прокси и клиенты склонны выбрасывать тело —
        ровно то, ради чего эндпоинт и существует. Диагноз ставит WARDEN.
        """
        started = time.perf_counter()
        checks = await health_checker.check()
        metrics = await health_checker.collect()
        return web.json_response(
            {
                "status": "healthy" if all(checks.values()) else "unhealthy",
                "checks": checks,
                "metrics": metrics,
                "uptime_seconds": health_checker.uptime_seconds(),
                "collect_duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "collected_at": datetime.now(UTC).isoformat(),
            }
        )

    app.router.add_get("/health", health_handler)
    app.router.add_get("/ready", health_handler)
    app.router.add_get("/health/full", health_full_handler)
    return app


async def start_health_server(health_checker: HealthChecker, port: int) -> web.AppRunner:
    """Запускается фоновой задачей в том же event loop, что и discord-клиент
    (ТЗ: отдельный таск внутри процесса бота, не отдельный сервис)."""
    runner = web.AppRunner(create_web_app(health_checker))
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Health-сервер запущен", extra={"port": port})
    return runner
