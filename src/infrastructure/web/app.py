import logging
from collections.abc import Awaitable, Callable

from aiohttp import web

logger = logging.getLogger(__name__)

HealthCheck = Callable[[], Awaitable[bool]]


class HealthChecker:
    """Реестр проверок здоровья (ТЗ 9.3). Упавшая проверка — это False,
    а не исключение наружу."""

    def __init__(self) -> None:
        self.checks: dict[str, HealthCheck] = {}

    def register(self, name: str, check: HealthCheck) -> None:
        self.checks[name] = check

    async def check(self) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for name, check_fn in self.checks.items():
            try:
                results[name] = bool(await check_fn())
            except Exception:
                logger.warning("Health-проверка упала", extra={"check": name}, exc_info=True)
                results[name] = False
        return results


def create_web_app(health_checker: HealthChecker) -> web.Application:
    app = web.Application()

    async def health_handler(request: web.Request) -> web.Response:
        status = await health_checker.check()
        if all(status.values()):
            return web.json_response({"status": "healthy", "checks": status})
        return web.json_response({"status": "unhealthy", "checks": status}, status=503)

    app.router.add_get("/health", health_handler)
    app.router.add_get("/ready", health_handler)
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
