import asyncio
import logging
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence

from src.application.interfaces.audio_source import IAudioSource
from src.application.interfaces.voice_connection import IVoiceConnection
from src.domain.events.bus import IEventBus
from src.domain.music.entities import RepeatMode, Track
from src.domain.music.events import TrackStarted
from src.domain.music.exceptions import TrackResolveError

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 50
# Прямые ссылки на аудиопоток YouTube живут часами; держим свежими полчаса,
# чтобы не отдать ffmpeg протухший URL.
STREAM_CACHE_TTL = 1800.0
STREAM_CACHE_SIZE = 4


class GuildPlayer:
    """Состояние и логика плеера одной гильдии: очередь, история, повтор,
    громкость, тайминг прогресса. Не знает про Discord — только порты."""

    def __init__(
        self,
        guild_id: int,
        audio_source: IAudioSource,
        voice: IVoiceConnection,
        event_bus: IEventBus,
        volume: float = 0.5,
        prefetch_files: int = 3,
    ):
        self.guild_id = guild_id
        self._audio = audio_source
        self._voice = voice
        self._bus = event_bus

        self.queue: deque[Track] = deque()
        self.history: list[Track] = []
        self.current: Track | None = None
        self.text_channel_id: int = 0  # выставляет ког при создании сообщения плеера
        self.repeat: RepeatMode = RepeatMode.OFF
        self.volume = volume
        self.is_paused = False

        # UI-хуки (устанавливает ког): обновить сообщение плеера / очередь кончилась
        self.on_state_changed: Callable[[], Awaitable[None]] | None = None
        self.on_idle: Callable[[], Awaitable[None]] | None = None

        self._loop: asyncio.AbstractEventLoop | None = None
        self._skip_requested = False
        self._suppress_history = False
        self._stopping = False
        self._started_mono: float | None = None
        self._paused_mono: float | None = None
        self._paused_total = 0.0
        # префетч аудиопотока следующего трека: убирает паузу ~1.5–3 с
        # (извлечение yt-dlp) между треками
        self._stream_cache: dict[str, tuple[float, str]] = {}
        self._prefetching: set[str] = set()
        self._background: set[asyncio.Task] = set()
        # скачивание следующих prefetch_files треков очереди на диск:
        # локальный файл играет без сетевых заиканий (стрим — только фолбэк)
        self._prefetch_files = max(0, prefetch_files)
        self._download_task: asyncio.Task | None = None
        self._download_failed: set[str] = set()  # не долбим умершие видео повторно

    @property
    def is_playing(self) -> bool:
        return self.current is not None

    def elapsed_precise(self) -> float:
        """Позиция трека в секундах с долями (для синхронизации караоке)."""
        if self._started_mono is None:
            return 0.0
        end = self._paused_mono if self._paused_mono is not None else time.monotonic()
        return max(0.0, end - self._started_mono - self._paused_total)

    def elapsed(self) -> int:
        """Сколько секунд трека проиграно (с учётом пауз)."""
        return int(self.elapsed_precise())

    async def enqueue(self, tracks: Sequence[Track], front: bool = False) -> None:
        if front:
            self.queue.extendleft(reversed(tracks))
        else:
            self.queue.extend(tracks)
        if self.current is None:
            await self._play_next()
        else:
            await self._notify()
            self._prefetch_next()  # очередь могла быть пустой — префетчим голову

    async def set_volume(self, value: float) -> None:
        """Абсолютная громкость 0.0–2.0 (для /volume 0–200%)."""
        self.volume = max(0.0, min(2.0, value))
        self._voice.set_volume(self.volume)
        await self._notify()

    def all_tracks(self) -> list[Track]:
        """Текущий трек + очередь (для сохранения плейлиста)."""
        result = list(self.queue)
        if self.current is not None:
            result.insert(0, self.current)
        return result

    async def skip(self) -> None:
        if self.current is None:
            await self._play_next()
            return
        self._skip_requested = True
        self._voice.stop()  # after-callback продвинет очередь

    async def previous(self) -> bool:
        """Возвращает False, если истории нет."""
        if not self.history:
            return False
        prev = self.history.pop()
        if self.current is not None:
            self.queue.appendleft(self.current)
        self.queue.appendleft(prev)
        self._suppress_history = True  # текущий уже возвращён в очередь
        if self.current is not None:
            self._skip_requested = True
            self._voice.stop()
        else:
            await self._play_next()
        return True

    async def toggle_pause(self) -> None:
        if self.current is None:
            return
        if self.is_paused:
            self._voice.resume()
            if self._paused_mono is not None:
                self._paused_total += time.monotonic() - self._paused_mono
                self._paused_mono = None
            self.is_paused = False
        else:
            self._voice.pause()
            self._paused_mono = time.monotonic()
            self.is_paused = True
        await self._notify()

    async def cycle_repeat(self) -> RepeatMode:
        order = [RepeatMode.OFF, RepeatMode.ONE, RepeatMode.ALL]
        self.repeat = order[(order.index(self.repeat) + 1) % len(order)]
        await self._notify()
        return self.repeat

    async def change_volume(self, delta: float) -> float:
        self.volume = min(2.0, max(0.0, round(self.volume + delta, 2)))
        self._voice.set_volume(self.volume)
        await self._notify()
        return self.volume

    async def shuffle(self) -> None:
        items = list(self.queue)
        random.shuffle(items)
        self.queue = deque(items)
        await self._notify()
        self._prefetch_next()  # голова очереди сменилась

    async def stop_and_clear(self) -> None:
        self._stopping = True
        for task in self._background:
            task.cancel()
        if self._download_task is not None:
            self._download_task.cancel()
        self._stream_cache.clear()
        self.queue.clear()
        self.history.clear()
        self.current = None
        self._reset_timing()
        try:
            self._voice.stop()
        except Exception:  # соединение могло уже умереть
            logger.debug("voice.stop() при остановке упал", exc_info=True)
        try:
            await self._voice.disconnect()
        except Exception:
            logger.debug("voice.disconnect() при остановке упал", exc_info=True)

    # --- внутреннее ---

    def _reset_timing(self) -> None:
        self._started_mono = None
        self._paused_mono = None
        self._paused_total = 0.0
        self.is_paused = False

    async def _play_next(self) -> None:
        while self.queue:
            track = self.queue.popleft()
            if await self._try_play(track):
                return
        self.current = None
        self._reset_timing()
        await self._notify()
        if self.on_idle:
            try:
                await self.on_idle()
            except Exception:
                logger.exception("on_idle-хук упал")

    def _cached_stream_url(self, video_id: str) -> str | None:
        entry = self._stream_cache.get(video_id)
        if entry is None:
            return None
        cached_at, url = entry
        if time.monotonic() - cached_at > STREAM_CACHE_TTL:
            del self._stream_cache[video_id]
            return None
        return url

    def _store_stream_url(self, video_id: str, url: str) -> None:
        self._stream_cache[video_id] = (time.monotonic(), url)
        while len(self._stream_cache) > STREAM_CACHE_SIZE:
            self._stream_cache.pop(next(iter(self._stream_cache)))

    def _prefetch_next(self) -> None:
        """Фоном подготовить следующие треки: скачивание файлов + запасной
        аудиопоток для головы очереди (если файл не успеет)."""
        self._start_file_prefetch()
        if not self.queue or self._stopping:
            return
        upcoming = self.queue[0]
        if (
            upcoming.video_id in self._prefetching
            or self._cached_stream_url(upcoming.video_id) is not None
            or self._audio.cached_path(upcoming) is not None
        ):
            return
        self._prefetching.add(upcoming.video_id)

        async def run() -> None:
            try:
                url = await self._audio.get_stream_url(upcoming)
                self._store_stream_url(upcoming.video_id, url)
            except Exception:
                # не страшно: при старте трека поток извлечётся обычным путём
                logger.debug("Префетч потока не удался", exc_info=True)
            finally:
                self._prefetching.discard(upcoming.video_id)

        task = asyncio.create_task(run())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _start_file_prefetch(self) -> None:
        """Фоном скачать следующие треки очереди в дисковый кэш."""
        if self._prefetch_files <= 0 or self._stopping:
            return
        if self._download_task is not None and not self._download_task.done():
            return
        self._download_task = asyncio.create_task(self._download_ahead())

    async def _download_ahead(self) -> None:
        # По одному треку за раз (замок в источнике тоже глобальный):
        # закачка не должна отбирать канал у живого стрима текущего трека.
        # После каждой закачки голова очереди перечитывается — её могли
        # перемешать или проскипать.
        while not self._stopping:
            target = next(
                (
                    t
                    for t in list(self.queue)[: self._prefetch_files]
                    if t.duration is not None  # live не скачивается
                    and t.video_id not in self._download_failed
                    and self._audio.cached_path(t) is None
                ),
                None,
            )
            if target is None:
                return
            try:
                path = await self._audio.download(target)
            except Exception:
                path = None
                logger.debug("Закачка трека в кэш не удалась", exc_info=True)
            if path is None:
                # не вышло — сыграем стримом; второй раз не пытаемся
                self._download_failed.add(target.video_id)

    async def _try_play(self, track: Track) -> bool:
        # приоритет — локальный файл из кэша: играет ровно, без сети
        stream_url = None if track.duration is None else self._audio.cached_path(track)
        if stream_url is None:
            stream_url = self._cached_stream_url(track.video_id)
        if stream_url is None:
            try:
                stream_url = await self._audio.get_stream_url(track)
            except TrackResolveError as exc:
                logger.warning(
                    "Не удалось получить аудиопоток, пропускаю трек",
                    extra={"track": track.title, "reason": str(exc)},
                )
                return False
            self._store_stream_url(track.video_id, stream_url)
        self._loop = asyncio.get_running_loop()
        self.current = track
        self._reset_timing()
        self._started_mono = time.monotonic()
        headers = self._audio.stream_headers(track.video_id)
        await self._voice.play(stream_url, self.volume, self._on_finished, headers)
        await self._bus.publish(
            TrackStarted(
                aggregate_id=str(self.guild_id),
                guild_id=self.guild_id,
                channel_id=self.text_channel_id,
                title=track.title,
                url=track.url,
                requested_by=track.requested_by,
            )
        )
        await self._notify()
        self._prefetch_next()
        return True

    def _on_finished(self, error: Exception | None) -> None:
        # Вызывается из аудио-потока discord.py — переносим в event loop.
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._advance(error)))

    async def _advance(self, error: Exception | None) -> None:
        if error:
            logger.error("Ошибка воспроизведения", extra={"error": str(error)})
        if self._stopping:
            return
        skipped, self._skip_requested = self._skip_requested, False
        suppress, self._suppress_history = self._suppress_history, False
        finished = self.current
        if finished is not None and not suppress:
            if self.repeat is RepeatMode.ONE and not skipped:
                if await self._try_play(finished):
                    return
            else:
                self.history.append(finished)
                del self.history[:-HISTORY_LIMIT]
                if self.repeat is RepeatMode.ALL:
                    self.queue.append(finished)
        await self._play_next()

    async def _notify(self) -> None:
        if self.on_state_changed:
            try:
                await self.on_state_changed()
            except Exception:
                logger.exception("Ошибка обновления сообщения плеера")
