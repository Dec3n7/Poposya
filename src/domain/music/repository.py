from abc import ABC, abstractmethod

from src.domain.music.entities import LikedTrack, Playlist


class IPlaylistRepository(ABC):
    """Сохранённые очереди сервера."""

    @abstractmethod
    async def get(self, guild_id: int, name: str) -> Playlist | None: ...

    @abstractmethod
    async def list_names(self, guild_id: int) -> list[tuple[str, int, int]]:
        """(название, число треков, created_by) по гильдии."""

    @abstractmethod
    async def count(self, guild_id: int) -> int: ...

    @abstractmethod
    async def save(self, playlist: Playlist) -> None:
        """Создаёт или перезаписывает плейлист с тем же именем."""

    @abstractmethod
    async def delete(self, guild_id: int, name: str) -> bool: ...


class ILikedTrackRepository(ABC):
    """Личные лайкнутые треки пользователей."""

    @abstractmethod
    async def get(self, user_id: int, video_id: str) -> LikedTrack | None: ...

    @abstractmethod
    async def add(self, liked: LikedTrack) -> None: ...

    @abstractmethod
    async def remove(self, user_id: int, video_id: str) -> bool: ...

    @abstractmethod
    async def list_for_user(self, user_id: int) -> list[LikedTrack]:
        """Новые сверху."""

    @abstractmethod
    async def count(self, user_id: int) -> int: ...

    @abstractmethod
    async def update_resolution(
        self, liked_id: int, video_id: str, title: str,
        uploader: str | None, duration: int | None,
    ) -> None:
        """Обновить метаданные после «оживления» умершего видео поиском."""
