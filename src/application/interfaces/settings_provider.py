from abc import ABC, abstractmethod


class ISettingsProvider(ABC):
    """Порт пер-гильдийных настроек. get возвращает значение сервера или
    переданный дефолт (обычно — глобальное значение из .env). Синхронный:
    реализация держит переопределения в памяти, горячий путь без запросов к БД."""

    @abstractmethod
    def get(self, guild_id: int, key: str, default):
        ...
