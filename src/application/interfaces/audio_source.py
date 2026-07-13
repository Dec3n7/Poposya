from abc import ABC, abstractmethod

from src.domain.music.entities import Track


class IAudioSource(ABC):
    """Порт поиска и получения аудиопотоков (реализация — yt-dlp в infrastructure)."""

    @abstractmethod
    async def search(self, query: str, requested_by: int, limit: int = 5) -> list[Track]: ...

    @abstractmethod
    async def resolve(self, url: str, requested_by: int, playlist_limit: int = 50) -> list[Track]:
        """Разворачивает ссылку в список треков (плейлист — целиком, до playlist_limit)."""

    @abstractmethod
    async def get_stream_url(self, track: Track) -> str:
        """Прямой URL аудиопотока; получается непосредственно перед проигрыванием,
        так как потоковые ссылки YouTube истекают."""

    # Кэш аудиофайлов — необязательная способность источника: дефолты
    # «кэша нет», чтобы фейки в тестах и простые реализации не менялись.

    def cached_path(self, track: Track) -> str | None:
        """Путь к скачанному файлу трека, если он уже в кэше на диске."""
        return None

    async def download(self, track: Track) -> str | None:
        """Скачать трек в кэш; None — скачивание недоступно (live, нет кэша)."""
        return None

    def track_meta(self, video_id: str) -> dict | None:
        """Доп. метаданные трека (просмотры/дата), если источник их запомнил.
        Дефолт — нет; необязательная способность."""
        return None

    def stream_headers(self, video_id: str) -> dict | None:
        """HTTP-заголовки для проигрывания потока (User-Agent и пр.). Нужны,
        чтобы ffmpeg не ловил 403 на клиент-специфичных ссылках. Дефолт — нет."""
        return None
