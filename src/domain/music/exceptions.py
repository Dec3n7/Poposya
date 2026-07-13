class MusicError(Exception):
    """Базовое исключение музыкальной фичи."""


class TrackResolveError(MusicError):
    """Не удалось получить информацию о треке или аудиопоток."""
