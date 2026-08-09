"""Метрики здоровья для внешнего мониторинга (WARDEN).

Ключевое отличие метрик от ворот: сбой сбора не имеет права ни уронить ответ,
ни исчезнуть из него — иначе мониторинг не отличит «метрику не удалось снять»
от «метрика равна нулю».
"""

import asyncio
import logging
import time

from aiohttp.test_utils import TestClient, TestServer

from src.infrastructure.listener_health import ListenerHealth
from src.infrastructure.logging.error_counter import ErrorRateCounter
from src.infrastructure.web.app import HealthChecker, create_web_app, measure_event_loop_lag


async def _client(checker: HealthChecker, full_token: str = "") -> TestClient:
    client = TestClient(TestServer(create_web_app(checker, full_token=full_token)))
    await client.start_server()
    return client


async def _ok() -> bool:
    return True


# --- реестр метрик ---


async def test_metrics_collected_by_group():
    checker = HealthChecker()
    checker.register_metric("gateway", lambda: _value({"latency_ms": 42.0}))

    assert await checker.collect() == {"gateway": {"latency_ms": 42.0}}


async def test_broken_provider_does_not_hide_the_others():
    checker = HealthChecker()

    async def boom() -> dict:
        raise RuntimeError("нет соединения")

    checker.register_metric("broken", boom)
    checker.register_metric("fine", lambda: _value({"pending": 0}))

    result = await checker.collect()
    assert result["fine"] == {"pending": 0}
    # группа осталась в ответе и объясняет себя, а не пропала молча
    assert "RuntimeError" in result["broken"]["error"]


async def _value(payload: dict) -> dict:
    return payload


# --- эндпоинты ---


async def test_health_contract_unchanged_by_metrics():
    """/health остаётся булевым: на нём висит docker healthcheck."""
    checker = HealthChecker()
    checker.register("database", _ok)
    checker.register_metric("gateway", lambda: _value({"latency_ms": 1.0}))

    client = await _client(checker)
    try:
        resp = await client.get("/health")
        assert resp.status == 200
        assert await resp.json() == {"status": "healthy", "checks": {"database": True}}
    finally:
        await client.close()


async def test_health_full_open_without_token():
    """Пустой токен = совместимость: /health/full открыт (как раньше)."""
    checker = HealthChecker()
    checker.register("database", _ok)
    client = await _client(checker)  # full_token=""
    try:
        assert (await client.get("/health/full")).status == 200
    finally:
        await client.close()


async def test_health_full_requires_token_when_set():
    checker = HealthChecker()
    checker.register("database", _ok)
    checker.register_metric("outbox", lambda: _value({"pending": 0}))
    client = await _client(checker, full_token="секрет-здоровья")
    try:
        # без заголовка — 401
        assert (await client.get("/health/full")).status == 401
        # с неверным — 401
        assert (await client.get("/health/full", headers={"X-Health-Token": "нет"})).status == 401
        # с верным — 200 и полное тело
        resp = await client.get("/health/full", headers={"X-Health-Token": "секрет-здоровья"})
        assert resp.status == 200
        assert (await resp.json())["metrics"]["outbox"] == {"pending": 0}
    finally:
        await client.close()


async def test_health_and_ready_stay_open_with_token():
    """/health и /ready не под токеном — на них docker healthcheck."""
    checker = HealthChecker()
    checker.register("database", _ok)
    client = await _client(checker, full_token="секрет-здоровья")
    try:
        assert (await client.get("/health")).status == 200
        assert (await client.get("/ready")).status == 200
    finally:
        await client.close()


async def test_health_full_returns_200_even_when_gates_closed():
    """Тело важнее кода: на 503 клиенты склонны выбрасывать полезную нагрузку,
    а именно ради неё WARDEN и ходит на этот эндпоинт."""
    checker = HealthChecker()

    async def down() -> bool:
        return False

    checker.register("database", down)
    checker.register_metric("outbox", lambda: _value({"pending": 7}))

    client = await _client(checker)
    try:
        resp = await client.get("/health/full")
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "unhealthy"
        assert body["checks"] == {"database": False}
        assert body["metrics"]["outbox"] == {"pending": 7}
        assert body["uptime_seconds"] >= 0
        assert "collected_at" in body
    finally:
        await client.close()


async def test_event_loop_lag_is_measured():
    lag = await measure_event_loop_lag()
    assert lag >= 0


# --- счётчик ошибок ---


def test_error_counter_counts_only_errors():
    counter = ErrorRateCounter(window_seconds=60)
    logger = logging.getLogger("test.errors")
    logger.addHandler(counter)
    try:
        logger.info("это не ошибка")
        logger.error("а это ошибка")
        logger.critical("и это тоже")
    finally:
        logger.removeHandler(counter)

    snapshot = counter.snapshot()
    assert snapshot["errors_in_window"] == 2
    assert snapshot["by_level"] == {"ERROR": 1, "CRITICAL": 1}
    assert snapshot["top_loggers"] == {"test.errors": 2}


def test_error_counter_forgets_outside_window():
    """Окно скользящее: счётчик с начала времён после первой же аварии
    навсегда остался бы большим и перестал что-либо значить."""
    counter = ErrorRateCounter(window_seconds=0.01)
    logger = logging.getLogger("test.errors.window")
    logger.addHandler(counter)
    try:
        logger.error("давняя ошибка")
    finally:
        logger.removeHandler(counter)
    time.sleep(0.05)  # пережидаем окно

    snapshot = counter.snapshot()
    assert snapshot["errors_in_window"] == 0
    # но факт, что ошибка вообще была, не теряется
    assert snapshot["total_since_start"] == 1


# --- состояние листенеров ---


def test_listener_first_connect_is_not_a_reconnect():
    health = ListenerHealth()
    health.mark_connected()

    snapshot = health.health_snapshot()
    assert snapshot["connected"] is True
    assert snapshot["reconnects"] == 0


def test_listener_reconnect_counted_and_disconnect_visible():
    health = ListenerHealth()
    health.mark_connected()
    health.mark_disconnected()
    health.mark_connected()

    snapshot = health.health_snapshot()
    assert snapshot["reconnects"] == 1
    assert snapshot["connected"] is True

    health.mark_disconnected()
    down = health.health_snapshot()
    assert down["connected"] is False
    assert down["connected_for_seconds"] is None
    assert down["seconds_since_last_connect"] is not None


def test_downtime_is_measured_from_the_break_not_from_the_last_connect():
    """Разные вещи, и их путали: `seconds_since_last_connect` включает всё
    время, что листенер был жив. Листенер, проработавший час и оборвавшийся
    секунду назад, по нему выглядел бы застрявшим на час — и любой штатный
    реконнект читался бы как авария."""
    health = ListenerHealth()
    health.mark_connected()
    health._lh_last_connected_at -= 3600  # как будто держал соединение час
    health.mark_disconnected()

    snapshot = health.health_snapshot()
    assert snapshot["seconds_since_last_connect"] > 3599
    assert snapshot["disconnected_for_seconds"] < 5


def test_downtime_keeps_growing_across_reconnect_attempts():
    """`mark_disconnected` зовётся на каждом витке цикла переподключения.
    Если бы отметка переписывалась, вечно лежащий листенер показывал бы ноль."""
    health = ListenerHealth()
    health.mark_connected()
    health.mark_disconnected()
    health._lh_disconnected_since -= 300  # лежит пять минут
    health.mark_disconnected()  # очередная провалившаяся попытка

    assert health.health_snapshot()["disconnected_for_seconds"] > 299


def test_successful_reconnect_clears_the_downtime():
    health = ListenerHealth()
    health.mark_disconnected()
    health._lh_disconnected_since -= 300
    health.mark_connected()

    assert health.health_snapshot()["disconnected_for_seconds"] is None


def test_never_connected_listener_counts_downtime_from_start():
    """Листенер, ни разу не поднявшийся с запуска, — самый тяжёлый случай:
    мост панель→бот не работал никогда. Он обязан быть видимым."""
    snapshot = ListenerHealth().health_snapshot()
    assert snapshot["connected"] is False
    assert snapshot["seconds_since_last_connect"] is None
    assert snapshot["disconnected_for_seconds"] is not None
    assert snapshot["reconnects"] == 0


# --- фоновые задачи ---


async def test_dead_background_task_is_named():
    """Главная дыра, ради которой всё затевалось: упавшая задача не видна
    ни по одному другому признаку."""
    from src.main import _background_tasks_metrics

    async def dies() -> None:
        raise RuntimeError("диспетчер умер")

    async def lives() -> None:
        await asyncio.sleep(3600)

    tasks = {"dead-one": asyncio.create_task(dies()), "alive-one": asyncio.create_task(lives())}
    await asyncio.sleep(0)  # дать умирающей задаче добежать до исключения

    try:
        stats = await _background_tasks_metrics(tasks)()
        assert stats["expected"] == 2
        assert stats["alive"] == 1
        assert "RuntimeError: диспетчер умер" in stats["dead"]["dead-one"]
        assert "alive-one" not in stats["dead"]
    finally:
        tasks["alive-one"].cancel()
