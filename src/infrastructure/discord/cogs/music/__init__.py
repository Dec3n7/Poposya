"""Музыкальный модуль: тонкий ког + сервисы (service/lyrics/radio) + views.

Публичная точка входа — MusicCog; import-путь совпадает с бывшим
одиночным файлом cogs/music.py."""

from src.infrastructure.discord.cogs.music.cog import MusicCog

__all__ = ["MusicCog"]
