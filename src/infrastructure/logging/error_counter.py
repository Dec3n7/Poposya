"""Счётчик ошибок в скользящем окне — метрика для внешнего мониторинга.

Обработчик логирования, а не отдельный код в местах ошибок: бот уже пишет
`logger.exception` везде, где что-то ломается, и перехват на уровне logging даёт
полное покрытие без правок в бизнес-логике.

Окно скользящее: WARDEN опрашивает бота регулярно и должен видеть «сколько
ошибок прямо сейчас», а не «сколько накопилось с запуска» — счётчик с начала
времён после первой же аварии навсегда останется большим.
"""

import logging
import threading
import time
from collections import Counter, deque

_DEFAULT_WINDOW = 900.0  # 15 минут — совпадает с шагом heartbeat в логе


class ErrorRateCounter(logging.Handler):
    """Считает записи уровня ERROR и выше за последние `window_seconds`.

    Обработчик вызывается из любого потока (миграции идут в executor), поэтому
    доступ к окну под локом. Исключение внутри обработчика гасится молча — сбой
    метрики не должен ломать логирование, ради которого всё и затевалось.
    """

    def __init__(self, window_seconds: float = _DEFAULT_WINDOW):
        super().__init__(level=logging.ERROR)
        self._window = window_seconds
        self._events: deque[tuple[float, str, str]] = deque()
        self._lock = threading.Lock()
        self._total_since_start = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            with self._lock:
                self._events.append((time.monotonic(), record.levelname, record.name))
                self._total_since_start += 1
                self._trim_locked()
        except Exception:  # pragma: no cover — защита логирования от самого себя
            pass

    def _trim_locked(self) -> None:
        cutoff = time.monotonic() - self._window
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def snapshot(self) -> dict:
        """Сколько ошибок в окне, как они разложены по уровням и какие логгеры
        шумят больше всех. Топ логгеров нужен, чтобы по одной метрике было видно
        не только «плохо», но и «где именно плохо»."""
        with self._lock:
            self._trim_locked()
            events = list(self._events)
            total_since_start = self._total_since_start

        by_level = Counter(level for _, level, _ in events)
        by_logger = Counter(name for _, _, name in events)
        return {
            "window_seconds": self._window,
            "errors_in_window": len(events),
            "by_level": dict(by_level),
            "top_loggers": dict(by_logger.most_common(3)),
            "total_since_start": total_since_start,
            "seconds_since_last": (round(time.monotonic() - events[-1][0], 1) if events else None),
        }


def install_error_counter(window_seconds: float = _DEFAULT_WINDOW) -> ErrorRateCounter:
    """Вешает счётчик на корневой логгер. Вызывать только после setup_logging:
    тот чистит root.handlers и снял бы счётчик вместе с остальными."""
    counter = ErrorRateCounter(window_seconds)
    logging.getLogger().addHandler(counter)
    return counter
