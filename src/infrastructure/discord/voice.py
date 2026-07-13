import shlex
from collections.abc import Callable

import discord

from src.application.interfaces.voice_connection import IVoiceConnection

# Переподключение к потоку при обрывах CDN YouTube
_FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
_FFMPEG_OPTS = "-vn"


def _headers_arg(headers: dict | None) -> str:
    """-headers для ffmpeg из HTTP-заголовков yt-dlp (лечит 403 от googlevideo)."""
    if not headers:
        return ""
    blob = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    return f"-headers {shlex.quote(blob)}"


class DiscordVoiceConnection(IVoiceConnection):
    def __init__(self, voice_client: discord.VoiceClient, ffmpeg_path: str = "ffmpeg"):
        self._vc = voice_client
        self._ffmpeg_path = ffmpeg_path
        self._source: discord.PCMVolumeTransformer | None = None

    async def play(
        self,
        stream_url: str,
        volume: float,
        on_finished: Callable[[Exception | None], None],
        headers: dict | None = None,
    ) -> None:
        # reconnect-флаги + HTTP-заголовки (User-Agent) — только для HTTP-стрима;
        # локальному файлу из кэша они не нужны (и дали бы предупреждения ffmpeg)
        is_remote = stream_url.startswith(("http://", "https://"))
        before = None
        if is_remote:
            before = " ".join(p for p in (_FFMPEG_BEFORE, _headers_arg(headers)) if p)
        audio = discord.FFmpegPCMAudio(
            stream_url,
            executable=self._ffmpeg_path,
            before_options=before,
            options=_FFMPEG_OPTS,
        )
        self._source = discord.PCMVolumeTransformer(audio, volume=volume)
        self._vc.play(self._source, after=on_finished)

    def pause(self) -> None:
        if self._vc.is_playing():
            self._vc.pause()

    def resume(self) -> None:
        if self._vc.is_paused():
            self._vc.resume()

    def stop(self) -> None:
        if self._vc.is_playing() or self._vc.is_paused():
            self._vc.stop()

    def set_volume(self, volume: float) -> None:
        if self._source is not None:
            self._source.volume = volume

    async def disconnect(self) -> None:
        await self._vc.disconnect(force=True)
