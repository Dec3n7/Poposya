from abc import ABC, abstractmethod
from collections.abc import Callable


class IVoiceConnection(ABC):
    """Порт голосового воспроизведения. Прячет discord.VoiceClient от
    application-слоя: GuildPlayer тестируется без Discord."""

    @abstractmethod
    async def play(
        self,
        stream_url: str,
        volume: float,
        on_finished: Callable[[Exception | None], None],
        headers: dict | None = None,
        seek_seconds: float = 0.0,
    ) -> None:
        """Начинает воспроизведение. headers — HTTP-заголовки для ffmpeg (User-Agent
        и пр.), чтобы не ловить 403 на клиент-специфичных ссылках. seek_seconds —
        начать не с начала, а с этой секунды (для /seek). on_finished вызывается
        по окончании трека (в т.ч. после stop()) и может прийти из другого потока."""

    @abstractmethod
    def pause(self) -> None: ...

    @abstractmethod
    def resume(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def set_volume(self, volume: float) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...
