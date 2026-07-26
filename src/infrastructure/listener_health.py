"""Состояние соединения LISTEN/NOTIFY-листенеров — для health-метрик.

Все три листенера (настройки, персоны, команды панели) переподключаются в
бесконечном цикле и потому никогда не «падают». Снаружи это неотличимо от
исправной работы: листенер, застрявший в переподключении из-за исчерпанного
пула или битого DSN, молча не доставляет ни одного NOTIFY.

Миксин делает это состояние наблюдаемым: подключён ли листенер прямо сейчас,
как давно, и сколько раз переподключался.
"""

import time


class ListenerHealth:
    """Подмешивается к листенеру; `mark_connected` / `mark_disconnected`
    вызываются вокруг живого соединения."""

    def __init__(self) -> None:
        self._lh_connected = False
        self._lh_connected_since: float | None = None
        self._lh_last_connected_at: float | None = None
        # Листенер начинает жизнь отключённым, и отсчёт идёт с этого момента:
        # «ни разу не подключился» — самый тяжёлый случай, и он не должен быть
        # неотличим от исправной работы.
        self._lh_disconnected_since: float | None = time.monotonic()
        self._lh_reconnects = 0

    def mark_connected(self) -> None:
        # первое подключение — не переподключение: счётчик растёт только начиная
        # со второго, иначе после каждого рестарта бота метрика врёт на единицу
        if self._lh_last_connected_at is not None:
            self._lh_reconnects += 1
        now = time.monotonic()
        self._lh_connected = True
        self._lh_connected_since = now
        self._lh_last_connected_at = now
        self._lh_disconnected_since = None

    def mark_disconnected(self) -> None:
        # Отметка ставится только на переходе. Метод зовётся из `finally` на
        # каждом витке переподключения, и переписывать её значило бы вечно
        # показывать «лежит ноль секунд» ровно у того листенера, который лежит
        # дольше всех.
        if self._lh_disconnected_since is None:
            self._lh_disconnected_since = time.monotonic()
        self._lh_connected = False
        self._lh_connected_since = None

    def health_snapshot(self) -> dict:
        now = time.monotonic()
        return {
            "connected": self._lh_connected,
            "connected_for_seconds": (
                round(now - self._lh_connected_since, 1)
                if self._lh_connected_since is not None
                else None
            ),
            # Возраст текущей линии соединения: сколько прошло с последнего
            # успешного подключения. Признаком застревания НЕ является — пока
            # листенер жив, это число просто растёт вместе с ним.
            "seconds_since_last_connect": (
                round(now - self._lh_last_connected_at, 1)
                if self._lh_last_connected_at is not None
                else None
            ),
            # Вот это и есть признак застревания: сколько листенер лежит прямо
            # сейчас. Штатный реконнект укладывается в секунды (пауза 3с), так
            # что десятки секунд означают провалившийся цикл переподключения, а
            # не разовый разрыв.
            "disconnected_for_seconds": (
                round(now - self._lh_disconnected_since, 1)
                if self._lh_disconnected_since is not None
                else None
            ),
            "reconnects": self._lh_reconnects,
        }
