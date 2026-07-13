"""LRU-кэш скачанных аудиофайлов на диске.

Файлы кладёт yt-dlp (имя — <video_id>.<ext>); кэш отвечает за поиск
и вытеснение старых при превышении лимита. LRU — по mtime: обращение
к файлу «освежает» его."""

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# временные файлы недокачанного yt-dlp — не считаем их кэшем
_TEMP_SUFFIXES = {".part", ".ytdl"}


class AudioCache:
    def __init__(self, directory: str | Path, max_bytes: int):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes

    def find(self, video_id: str) -> Path | None:
        """Готовый файл трека; обращение продлевает ему жизнь в LRU."""
        for path in self.directory.glob(f"{video_id}.*"):
            if path.suffix in _TEMP_SUFFIXES:
                continue
            try:
                os.utime(path, (time.time(), time.time()))
            except OSError:
                pass  # файл мог исчезнуть между glob и utime
            return path
        return None

    def prune(self) -> None:
        """Удалить самые старые файлы, пока кэш не влезет в лимит."""
        files = [
            p for p in self.directory.iterdir() if p.is_file() and p.suffix not in _TEMP_SUFFIXES
        ]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)  # свежие первыми
        total = 0
        for path in files:
            total += path.stat().st_size
            if total > self._max_bytes:
                try:
                    path.unlink()
                    logger.debug("Кэш аудио: вытеснен %s", path.name)
                except OSError:
                    pass  # файл может играть прямо сейчас (Windows) — снесём в следующий раз
