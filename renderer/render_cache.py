"""Content-addressed кэш готовых PNG (рычаг A2 из docs/plans/scale-300-guilds.md).

Бот шлёт renderer самодостаточный HTML (аватар уже вшит data-URI), поэтому
identical HTML + размеры + scale ⇒ identical PNG. Ключ = sha256 этого набора,
значение — сами байты PNG. Кэш корректен по построению: одинаковый вход даёт
побитово одинаковый выход, инвалидация не нужна.

Зачем: ачивки почти статичны, `/rank` повторяется в пределах уровня/аватара —
кэш схлопывает 80–95% рендеров ещё до всякой смены движка, снимая доминирующего
потребителя CPU/RAM (Chromium, см. perf-baseline.md).

Только stdlib: модуль живёт в образе renderer рядом с server.py и не тянет
playwright — его можно юнит-тестировать без браузера. Renderer — один asyncio-
цикл, поэтому синхронные get/put без await между собой гонки не создают; на два
конкурентных промаха по одному ключу худшее — лишний повторный рендер, put
идемпотентен.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict


def render_key(html: str, width: int, height: int, scale: int) -> str:
    """Стабильный ключ рендера. Разделитель `\\x00` не встречается в тексте HTML,
    поэтому склейка полей однозначна (нельзя подобрать другой набор с тем же
    ключом смещением границ)."""
    h = hashlib.sha256()
    h.update(html.encode("utf-8"))
    h.update(b"\x00")
    h.update(f"{width}x{height}@{scale}".encode())
    return h.hexdigest()


class RenderCache:
    """LRU готовых PNG с двойной границей: по числу записей и по суммарным байтам.

    Байтовая граница — главная: PNG-карточки разнятся в разы (десятки КБ … ~1 МБ),
    и лимит по одному лишь количеству либо раздувал бы память на тяжёлых карточках,
    либо зря вытеснял бы лёгкие. Значение крупнее всего бюджета не кэшируется вовсе
    (иначе один гигант вытеснил бы всё остальное ради самого себя)."""

    def __init__(self, max_entries: int = 512, max_bytes: int = 256 * 1024 * 1024) -> None:
        self._max_entries = max(0, max_entries)
        self._max_bytes = max(0, max_bytes)
        self._store: OrderedDict[str, bytes] = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0

    @property
    def enabled(self) -> bool:
        return self._max_entries > 0 and self._max_bytes > 0

    def get(self, key: str) -> bytes | None:
        value = self._store.get(key)
        if value is None:
            self.misses += 1
            return None
        self._store.move_to_end(key)  # обращение делает запись самой свежей (MRU)
        self.hits += 1
        return value

    def put(self, key: str, value: bytes) -> None:
        if not self.enabled or len(value) > self._max_bytes:
            return
        if key in self._store:
            self._bytes -= len(self._store.pop(key))
        self._store[key] = value
        self._bytes += len(value)
        self._evict()

    def _evict(self) -> None:
        while self._store and (
            len(self._store) > self._max_entries or self._bytes > self._max_bytes
        ):
            _, evicted = self._store.popitem(last=False)  # last=False → самый старый (LRU)
            self._bytes -= len(evicted)

    def stats(self) -> dict[str, int | float]:
        total = self.hits + self.misses
        return {
            "entries": len(self._store),
            "bytes": self._bytes,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
        }
