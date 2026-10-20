"""Contract-тест health-схемы бот↔WARDEN (ADR-018).

Health API — единственная связь Poposya с WARDEN (ADR-004). Изменение формата
`/health/full` не имеет права молча сломать автономное лечение, поэтому состав,
типы и версия схемы закреплены здесь: тест падает при ломающем изменении и
заставляет либо поднять `HEALTH_SCHEMA_VERSION`, либо подтвердить обратную
совместимость.
"""

from aiohttp.test_utils import TestClient, TestServer

from src.infrastructure.web.app import (
    HEALTH_SCHEMA_VERSION,
    HealthChecker,
    create_web_app,
)

# Поля, которые WARDEN читает из /health/full. Набор — контракт: удаление или
# переименование любого из них требует подъёма HEALTH_SCHEMA_VERSION.
_REQUIRED_FULL_FIELDS = {
    "status",
    "health_schema_version",
    "checks",
    "metrics",
    "uptime_seconds",
    "collect_duration_ms",
    "collected_at",
}


async def _client(checker: HealthChecker, full_token: str = "") -> TestClient:
    client = TestClient(TestServer(create_web_app(checker, full_token=full_token)))
    await client.start_server()
    return client


async def _ok() -> bool:
    return True


async def _value(payload: dict) -> dict:
    return payload


async def test_full_schema_has_version_and_required_fields():
    checker = HealthChecker()
    checker.register("database", _ok)
    checker.register_metric("outbox", lambda: _value({"pending": 0}))

    client = await _client(checker)
    try:
        resp = await client.get("/health/full")
        assert resp.status == 200
        body = await resp.json()
    finally:
        await client.close()

    assert set(body.keys()) >= _REQUIRED_FULL_FIELDS
    assert body["health_schema_version"] == HEALTH_SCHEMA_VERSION
    assert isinstance(body["health_schema_version"], int)
    assert body["status"] in {"healthy", "unhealthy"}
    assert isinstance(body["checks"], dict)
    assert isinstance(body["metrics"], dict)
    assert isinstance(body["uptime_seconds"], int | float)
    assert isinstance(body["collect_duration_ms"], int | float)
    assert isinstance(body["collected_at"], str)


async def test_full_schema_version_present_even_when_unhealthy():
    """Версия приходит и при закрытых воротах: эндпоинт всегда 200 и отдаёт тело."""
    checker = HealthChecker()

    async def down() -> bool:
        return False

    checker.register("database", down)

    client = await _client(checker)
    try:
        body = await (await client.get("/health/full")).json()
    finally:
        await client.close()

    assert body["status"] == "unhealthy"
    assert body["health_schema_version"] == HEALTH_SCHEMA_VERSION
