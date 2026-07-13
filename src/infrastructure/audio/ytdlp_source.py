import asyncio
import logging
from typing import Any

import yt_dlp

from src.application.interfaces.audio_source import IAudioSource
from src.domain.music.entities import Track
from src.domain.music.exceptions import TrackResolveError
from src.infrastructure.audio.cache import AudioCache

logger = logging.getLogger(__name__)

_BASE_OPTS: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": False,
    "format": "bestaudio/best",
    "skip_download": True,
}


class YtDlpAudioSource(IAudioSource):
    def __init__(
        self,
        cookies_from_browser: str | None = None,
        cookies_file: str | None = None,
        cache: AudioCache | None = None,
    ):
        # Cookies нужны, когда YouTube отвечает "Sign in to confirm you're not a bot"
        self._cookies_from_browser = cookies_from_browser
        self._cookies_file = cookies_file
        self._cache = cache
        # одна закачка за раз: не отбираем канал у живого стрима текущего трека
        self._download_lock = asyncio.Lock()
        # метаданные (просмотры/дата) из полного извлечения при старте трека —
        # берём бесплатно из того же вызова, что достаёт аудиопоток
        self._meta: dict[str, dict] = {}
        # HTTP-заголовки формата (в т.ч. User-Agent): без них ffmpeg ловит 403
        # на ссылках, привязанных к клиенту (например c=ANDROID_VR)
        self._headers: dict[str, dict] = {}

    async def search(self, query: str, requested_by: int, limit: int = 5) -> list[Track]:
        info = await self._extract(f"ytsearch{limit}:{query}", flat=True)
        entries = (info or {}).get("entries") or []
        return [self._entry_to_track(e, requested_by) for e in entries if e]

    async def resolve(self, url: str, requested_by: int, playlist_limit: int = 50) -> list[Track]:
        info = await self._extract(url, flat=True)
        if not info:
            raise TrackResolveError("пустой ответ от YouTube")
        if "entries" in info:  # плейлист
            entries = [e for e in (info["entries"] or []) if e][:playlist_limit]
            return [self._entry_to_track(e, requested_by) for e in entries]
        return [self._entry_to_track(info, requested_by)]

    def track_meta(self, video_id: str) -> dict | None:
        """Просмотры/дата загрузки, если трек уже извлекался (для now-playing)."""
        return self._meta.get(video_id)

    def stream_headers(self, video_id: str) -> dict | None:
        """HTTP-заголовки последнего извлечённого потока (User-Agent и пр.)."""
        return self._headers.get(video_id)

    def _remember_meta(self, video_id: str, info: dict) -> None:
        if not video_id:
            return
        self._meta[video_id] = {
            "view_count": info.get("view_count"),
            "upload_date": info.get("upload_date"),  # YYYYMMDD
        }
        while len(self._meta) > 64:
            self._meta.pop(next(iter(self._meta)))

    def _remember_headers(self, video_id: str, headers: dict | None) -> None:
        if not video_id or not headers:
            return
        self._headers[video_id] = dict(headers)
        while len(self._headers) > 64:
            self._headers.pop(next(iter(self._headers)))

    async def get_stream_url(self, track: Track) -> str:
        info = await self._extract(track.url, flat=False)
        if not info:
            raise TrackResolveError(f"нет данных для {track.url}")
        self._remember_meta(track.video_id, info)
        stream_url = info.get("url")
        headers = info.get("http_headers")
        if not stream_url:
            for fmt in reversed(info.get("formats") or []):
                if fmt.get("acodec") not in (None, "none") and fmt.get("url"):
                    stream_url = fmt["url"]
                    headers = fmt.get("http_headers") or headers
                    break
        if not stream_url:
            raise TrackResolveError(f"не найден аудиопоток для {track.title}")
        self._remember_headers(track.video_id, headers)
        return stream_url

    def cached_path(self, track: Track) -> str | None:
        if self._cache is None:
            return None
        path = self._cache.find(track.video_id)
        return str(path) if path is not None else None

    async def download(self, track: Track) -> str | None:
        """Скачать аудио трека в кэш (без перекодирования — ffmpeg читает
        webm/m4a напрямую). None — кэш выключен или это live-эфир."""
        if self._cache is None or track.duration is None:
            return None
        async with self._download_lock:
            existing = self._cache.find(track.video_id)
            if existing is not None:  # скачали, пока ждали очередь на закачку
                return str(existing)
            opts = self._opts_with_cookies(
                {
                    **_BASE_OPTS,
                    "skip_download": False,
                    "noplaylist": True,
                    "outtmpl": str(self._cache.directory / "%(id)s.%(ext)s"),
                }
            )

            def run() -> str:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(track.url, download=True)
                    return ydl.prepare_filename(info)

            loop = asyncio.get_running_loop()
            try:
                path = await loop.run_in_executor(None, run)
            except yt_dlp.utils.DownloadError as exc:
                raise TrackResolveError(str(exc)) from exc
            self._cache.prune()
            return path

    def _opts_with_cookies(self, opts: dict) -> dict:
        if self._cookies_from_browser:
            opts["cookiesfrombrowser"] = (self._cookies_from_browser,)
        elif self._cookies_file:
            opts["cookiefile"] = self._cookies_file
        return opts

    async def _extract(self, target: str, flat: bool) -> dict | None:
        opts = dict(_BASE_OPTS)
        if flat:
            # плейлисты/поиск разворачиваются без запроса каждого видео
            opts["extract_flat"] = "in_playlist"
        self._opts_with_cookies(opts)

        def run() -> dict | None:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(target, download=False)

        loop = asyncio.get_running_loop()
        try:
            # yt-dlp блокирующий — уводим в поток, чтобы не вешать event loop
            return await loop.run_in_executor(None, run)
        except yt_dlp.utils.DownloadError as exc:
            raise TrackResolveError(str(exc)) from exc

    @staticmethod
    def _entry_to_track(entry: dict, requested_by: int) -> Track:
        video_id = entry.get("id") or ""
        url = (
            entry.get("webpage_url")
            or entry.get("url")
            or f"https://www.youtube.com/watch?v={video_id}"
        )
        raw_duration = entry.get("duration")
        duration = int(raw_duration) if raw_duration else None
        return Track(
            video_id=video_id,
            title=entry.get("title") or "Без названия",
            url=url,
            duration=duration,
            requested_by=requested_by,
            uploader=entry.get("uploader") or entry.get("channel"),
            thumbnail=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else None,
        )
