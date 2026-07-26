"""Сборщики метрик для внешнего мониторинга (WARDEN).

Долг, зафиксированный в спеке сторожа: эти четыре сборщика были проверены
только живым `curl` на счастливом пути, а регрессию в них ничего не ловило.
Между тем именно они решают, увидит ли WARDEN аварию: молча отдающий нули
сборщик хуже отсутствующего — он выглядит как «всё хорошо».

Отдельно от `test_health_metrics.py`: там реестр и эндпоинт, здесь — сами
поставщики чисел.
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.domain.relationship.events import RelationshipRoleChanged
from src.infrastructure.db.models.outbox import OutboxEventModel
from src.infrastructure.discord.client import PoposyaBot
from src.infrastructure.events.in_memory_bus import InMemoryEventBus
from src.infrastructure.events.outbox import OutboxDispatcher, outbox_row_for
from src.main import (
    DatabaseProbe,
    _background_tasks_metrics,
    _listeners_metrics,
    _runtime_metrics,
)

# --- проба БД ---


class FakeConnection:
    def __init__(self, error=None, delay: float = 0.0):
        self._error = error
        self._delay = delay

    async def execute(self, statement):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error:
            raise self._error
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeEngine:
    """Движок ровно в той части, которой пользуется проба."""

    def __init__(self, error=None, delay: float = 0.0, pool=None):
        self._error = error
        self._delay = delay
        self.pool = pool

    def connect(self):
        return FakeConnection(self._error, self._delay)


class FakePool:
    def __init__(self, size=5, checked_out=2, broken=False):
        self._size = size
        self._checked_out = checked_out
        self._broken = broken

    def size(self):
        if self._broken:
            raise RuntimeError("пул не отвечает")
        return self._size

    def checkedout(self):
        return self._checked_out


async def test_database_probe_measures_latency_on_the_gate_check():
    """Ворота и метрика питаются от одного `SELECT 1`: второй поход в БД ради
    того же числа был бы лишней нагрузкой ровно тогда, когда база уже страдает."""
    probe = DatabaseProbe(FakeEngine(delay=0.01))

    assert await probe.check() is True
    metrics = await probe.metrics()

    assert metrics["reachable"] is True
    assert metrics["latency_ms"] >= 10


async def test_database_metrics_before_the_first_check_are_empty_not_zero():
    """Ноль означал бы «база отвечает мгновенно» — это не то же самое, что
    «мы её ещё не спрашивали»."""
    metrics = await DatabaseProbe(FakeEngine()).metrics()

    assert metrics["latency_ms"] is None
    assert metrics["reachable"] is None


async def test_failed_check_is_raised_and_recorded():
    """Исключение обязано уйти наверх: на нём стоят ворота `/health`, от
    которых зависит docker healthcheck."""
    probe = DatabaseProbe(FakeEngine(error=RuntimeError("база недоступна")))

    with pytest.raises(RuntimeError):
        await probe.check()

    metrics = await probe.metrics()
    assert metrics["reachable"] is False
    assert metrics["latency_ms"] is None  # прошлое измерение не выдаётся за свежее


async def test_pool_stats_are_included_when_available():
    probe = DatabaseProbe(FakeEngine(pool=FakePool(size=5, checked_out=2)))
    await probe.check()

    metrics = await probe.metrics()
    assert metrics["pool_size"] == 5
    assert metrics["pool_checked_out"] == 2


async def test_pool_stats_are_optional():
    """У SQLite-пула этих методов нет — метрика опциональна, а не обязательна."""
    metrics = await DatabaseProbe(FakeEngine(pool=object())).metrics()

    assert "pool_size" not in metrics
    assert "latency_ms" in metrics  # остальное на месте


async def test_broken_pool_does_not_break_the_group():
    metrics = await DatabaseProbe(FakeEngine(pool=FakePool(broken=True))).metrics()

    assert "pool_size" not in metrics
    assert metrics["pool_checked_out"] == 2  # соседняя метрика уцелела


# --- фоновые задачи ---


async def _forever():
    await asyncio.Event().wait()


async def _boom():
    raise ValueError("задача упала")


async def _finish():
    return None


async def test_all_background_tasks_alive():
    task = asyncio.create_task(_forever())
    try:
        metrics = await _background_tasks_metrics({"outbox": task})()

        assert metrics == {
            "expected": 1,
            "alive": 1,
            "dead": {},
            "names": ["outbox"],
        }
    finally:
        task.cancel()


async def test_crashed_task_is_named_with_its_exception():
    """Самая важная метрика набора: задача падает, исключение оседает внутри
    объекта Task, бот работает дальше, `/health` зелёный — а функция мертва до
    ближайшего рестарта."""
    task = asyncio.create_task(_boom())
    await asyncio.sleep(0)  # даём задаче упасть

    metrics = await _background_tasks_metrics({"outbox-dispatcher": task})()

    assert metrics["alive"] == 0
    assert "ValueError" in metrics["dead"]["outbox-dispatcher"]
    assert "задача упала" in metrics["dead"]["outbox-dispatcher"]


async def test_cancelled_task_is_reported_as_such():
    task = asyncio.create_task(_forever())
    task.cancel()
    await asyncio.sleep(0)

    metrics = await _background_tasks_metrics({"backup": task})()
    assert metrics["dead"] == {"backup": "cancelled"}


async def test_quietly_finished_task_counts_as_dead():
    """Вечный цикл, вернувший управление, — тоже отказ: он больше ничего не
    делает, хотя исключения не было."""
    task = asyncio.create_task(_finish())
    await asyncio.sleep(0)

    metrics = await _background_tasks_metrics({"command-listener": task})()
    assert metrics["dead"] == {"command-listener": "finished"}


async def test_dead_tasks_are_named_not_counted():
    """«Одна из пяти умерла» не даёт понять, что именно сломалось."""
    alive = asyncio.create_task(_forever())
    dead = asyncio.create_task(_boom())
    await asyncio.sleep(0)
    try:
        metrics = await _background_tasks_metrics({"живая": alive, "мёртвая": dead})()

        assert metrics["expected"] == 2 and metrics["alive"] == 1
        assert list(metrics["dead"]) == ["мёртвая"]
    finally:
        alive.cancel()


async def test_registry_is_read_at_collection_time():
    """Health-сервер поднимается раньше фоновых задач и не должен их ждать:
    метрика ссылается на словарь, который наполняется позже."""
    registry: dict[str, asyncio.Task] = {}
    collect = _background_tasks_metrics(registry)

    assert (await collect())["expected"] == 0

    task = asyncio.create_task(_forever())
    registry["outbox"] = task
    try:
        assert (await collect())["expected"] == 1
    finally:
        task.cancel()


# --- листенеры ---


class FakeListener:
    def __init__(self, snapshot: dict):
        self._snapshot = snapshot

    def health_snapshot(self) -> dict:
        return self._snapshot


async def test_listener_snapshots_are_grouped_by_name():
    metrics = await _listeners_metrics(
        {
            "settings": FakeListener({"connected": True, "reconnects": 0}),
            "command": FakeListener({"connected": False, "reconnects": 12}),
        }
    )()

    assert metrics["settings"]["connected"] is True
    assert metrics["command"]["reconnects"] == 12


async def test_no_listeners_on_sqlite():
    """На SQLite листенеров нет вовсе — пустая группа, а не отсутствие группы."""
    assert await _listeners_metrics({})() == {}


# --- рантайм ---


async def test_runtime_metrics_report_lag_and_task_count():
    metrics = await _runtime_metrics()

    assert metrics["event_loop_lag_ms"] >= 0  # отрицательной задержки не бывает
    assert metrics["asyncio_tasks"] >= 1


# --- шлюз и коги ---


class FakeBot:
    """Стенд для чистых методов PoposyaBot: полноценный бот тянет за собой
    контейнер, Discord и коги — ради арифметики по счётчикам это лишнее."""

    gateway_stats = PoposyaBot.gateway_stats
    cogs_stats = PoposyaBot.cogs_stats
    _trim_connection_windows = PoposyaBot._trim_connection_windows

    def __init__(self, latency=0.156, ready=True, cogs=None, expected=None):
        from collections import deque

        self._latency = latency
        self._ready = ready
        self.cogs = cogs if cogs is not None else {}
        self._expected_cogs = frozenset(expected if expected is not None else self.cogs)
        self._disconnect_times: deque = deque()
        self._resume_times: deque = deque()
        self._ready_at = time.monotonic()
        self.guilds = [object(), object()]
        self.voice_clients = []

    @property
    def latency(self):
        return self._latency

    def is_ready(self):
        return self._ready


def test_gateway_latency_is_reported_in_milliseconds():
    stats = FakeBot(latency=0.156).gateway_stats()

    assert stats["latency_ms"] == 156.0
    assert stats["ready"] is True
    assert stats["guilds"] == 2


def test_nan_latency_becomes_none_not_invalid_json():
    """discord.py отдаёт NaN до первого heartbeat, а `json_response` выдал бы
    литерал NaN — невалидный JSON, на котором зонд сторожа споткнётся."""
    assert FakeBot(latency=float("nan")).gateway_stats()["latency_ms"] is None


def test_infinite_latency_becomes_none():
    assert FakeBot(latency=float("inf")).gateway_stats()["latency_ms"] is None


def test_disconnect_window_counts_the_last_hour():
    """Важна не общая сумма разрывов с запуска, а то, штормит ли соединение
    прямо сейчас."""
    bot = FakeBot()
    now = time.monotonic()
    bot._disconnect_times.extend([now - 7200, now - 3000, now - 60])
    bot._resume_times.extend([now - 7200, now - 60])

    stats = bot.gateway_stats()

    assert stats["disconnects_last_hour"] == 2
    assert stats["resumes_last_hour"] == 1


def test_seconds_since_ready_is_none_before_the_first_connect():
    bot = FakeBot()
    bot._ready_at = None
    assert bot.gateway_stats()["seconds_since_ready"] is None


def test_cogs_match_the_baseline_when_nothing_fell_off():
    stats = FakeBot(cogs={"MusicCog": 1, "FunCog": 2}).cogs_stats()

    assert stats == {"loaded": 2, "expected": 2, "missing": []}


def test_missing_cog_is_named():
    """Расхождение с эталоном значит, что модуль отвалился на ходу: бот жив, а
    часть функций молча исчезла."""
    stats = FakeBot(
        cogs={"MusicCog": 1}, expected=["MusicCog", "CinemaCog", "FindsCog"]
    ).cogs_stats()

    assert stats["loaded"] == 1 and stats["expected"] == 3
    assert stats["missing"] == ["CinemaCog", "FindsCog"]  # отсортированы


def test_baseline_is_taken_from_what_actually_loaded():
    """Часть когов подключается условно (ai_chat — только с ключом Groq),
    поэтому эталон — факт успешной загрузки, а не константа."""
    stats = FakeBot(cogs={"MusicCog": 1}, expected=[]).cogs_stats()
    assert stats["missing"] == []


# --- очередь outbox ---


def _event(user_id: int = 1) -> RelationshipRoleChanged:
    return RelationshipRoleChanged(
        aggregate_id=f"10:{user_id}",
        guild_id=10,
        user_id=user_id,
        new_role_index=3,
        points=700,
    )


def _dispatcher(session_factory, max_attempts: int = 10) -> OutboxDispatcher:
    return OutboxDispatcher(
        session_factory, InMemoryEventBus(), interval_seconds=60, max_attempts=max_attempts
    )


async def test_empty_outbox_reports_zeroes(session_factory):
    stats = await _dispatcher(session_factory).backlog_stats()

    assert stats["pending"] == 0 and stats["dead"] == 0
    assert stats["oldest_pending_age_seconds"] is None
    assert stats["interval_seconds"] == 60


async def test_pending_events_are_counted_with_their_age(session_factory):
    async with session_factory() as session:
        row = outbox_row_for(_event())
        row.occurred_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=300)
        session.add(row)
        await session.commit()

    stats = await _dispatcher(session_factory).backlog_stats()

    assert stats["pending"] == 1
    assert 290 < stats["oldest_pending_age_seconds"] < 320


async def test_exhausted_events_are_counted_separately(session_factory):
    """Тихо растущий `dead` означает потерянные доменные события при формально
    исправном боте: диспетчер их больше не берёт, и сами они не уедут никогда."""
    async with session_factory() as session:
        alive = outbox_row_for(_event(1))
        exhausted = outbox_row_for(_event(2))
        exhausted.attempts = 10
        session.add_all([alive, exhausted])
        await session.commit()

    stats = await _dispatcher(session_factory, max_attempts=10).backlog_stats()

    assert stats["pending"] == 1  # исчерпавшее попытки в очередь не входит
    assert stats["dead"] == 1


async def test_published_events_are_not_a_backlog(session_factory):
    async with session_factory() as session:
        row = outbox_row_for(_event())
        row.published_at = datetime.now(UTC).replace(tzinfo=None)
        session.add(row)
        await session.commit()

    stats = await _dispatcher(session_factory).backlog_stats()
    assert stats["pending"] == 0 and stats["dead"] == 0


async def test_dispatcher_liveness_is_unknown_before_the_first_pass(session_factory):
    """Задача может быть «не завершена», но висеть внутри прохода — по одному
    факту существования таска работающий диспетчер от зависшего не отличить."""
    stats = await _dispatcher(session_factory).backlog_stats()
    assert stats["seconds_since_last_pass"] is None


async def test_liveness_mark_is_set_after_a_pass(session_factory):
    dispatcher = _dispatcher(session_factory)
    await dispatcher.dispatch_once()
    dispatcher._last_pass_at = time.monotonic()

    stats = await dispatcher.backlog_stats()
    assert 0 <= stats["seconds_since_last_pass"] < 5


async def test_liveness_mark_is_set_even_after_a_broken_pass(session_factory, monkeypatch):
    """Отметка ставится и после сбойного прохода: цикл жив, а качество проходов
    видно по отдельной метрике ошибок. Иначе одна ошибка выглядела бы как
    остановка диспетчера."""
    dispatcher = _dispatcher(session_factory)

    async def broken():
        raise RuntimeError("шина отвалилась")

    monkeypatch.setattr(dispatcher, "dispatch_once", broken)

    async def stop_after_one(_seconds):
        if dispatcher._last_pass_at is not None:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", stop_after_one)

    with pytest.raises(asyncio.CancelledError):
        await dispatcher.run_forever()

    assert dispatcher._last_pass_at is not None


async def test_backlog_stats_survive_a_growing_queue(session_factory):
    async with session_factory() as session:
        session.add_all([outbox_row_for(_event(i)) for i in range(1, 6)])
        await session.commit()

    stats = await _dispatcher(session_factory).backlog_stats()
    assert stats["pending"] == 5

    # и очередь действительно та же, что видит диспетчер
    async with session_factory() as session:
        rows = (await session.execute(select(OutboxEventModel))).scalars().all()
    assert len(rows) == 5
