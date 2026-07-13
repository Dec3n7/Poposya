from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.cinema.entities import MovieEntry, MovieNight


class IMovieEntryRepository(ABC):
    """Вотчлист и золотой фонд сервера."""

    @abstractmethod
    async def add(self, entry: MovieEntry) -> MovieEntry:
        """Сохраняет и возвращает с заполненным id."""

    @abstractmethod
    async def save(self, entry: MovieEntry) -> None: ...

    @abstractmethod
    async def get(self, entry_id: int) -> MovieEntry | None: ...

    @abstractmethod
    async def get_by_message(self, message_id: int) -> MovieEntry | None: ...

    @abstractmethod
    async def get_by_rating_message(self, message_id: int) -> MovieEntry | None: ...

    @abstractmethod
    async def find_listed_duplicate(
        self, guild_id: int, tmdb_id: int | None, title_lower: str
    ) -> MovieEntry | None: ...

    @abstractmethod
    async def count_listed(self, guild_id: int) -> int: ...

    @abstractmethod
    async def count_proposed(self, guild_id: int, user_id: int) -> int:
        """Сколько фильмов предложил пользователь (в любом статусе)."""

    @abstractmethod
    async def list_ranked(self, guild_id: int) -> list[tuple[MovieEntry, int, int]]:
        """listed-фильмы с голосами (entry, up, down), по чистому счёту."""

    @abstractmethod
    async def list_watched(self, guild_id: int) -> list[MovieEntry]:
        """По среднему баллу, убывание."""

    @abstractmethod
    async def list_rating_pending(self) -> list[MovieEntry]:
        """Фильмы в статусе rating всех гильдий (восстановление таймеров)."""

    @abstractmethod
    async def delete(self, entry_id: int) -> bool: ...

    @abstractmethod
    async def get_vote(self, entry_id: int, user_id: int) -> int | None: ...

    @abstractmethod
    async def set_vote(self, entry_id: int, user_id: int, value: int) -> None:
        """value: +1 / -1; upsert."""

    @abstractmethod
    async def remove_vote(self, entry_id: int, user_id: int) -> None: ...

    @abstractmethod
    async def vote_counts(self, entry_id: int) -> tuple[int, int]:
        """(за, против)."""


class IMovieNightRepository(ABC):
    @abstractmethod
    async def add(self, night: MovieNight) -> MovieNight: ...

    @abstractmethod
    async def save(self, night: MovieNight) -> None: ...

    @abstractmethod
    async def get(self, night_id: int) -> MovieNight | None: ...

    @abstractmethod
    async def get_by_poll_message(self, message_id: int) -> MovieNight | None: ...

    @abstractmethod
    async def get_by_winner_message(self, message_id: int) -> MovieNight | None: ...

    @abstractmethod
    async def get_active(self, guild_id: int) -> MovieNight | None:
        """Ночь в статусе poll или scheduled (одна на сервер)."""

    @abstractmethod
    async def list_pending(self) -> list[MovieNight]:
        """poll/scheduled всех гильдий (восстановление таймеров)."""

    @abstractmethod
    async def finish_by_entry(self, entry_id: int) -> None:
        """Ночь с этим победителем — в done (вызывается при финализации оценок)."""

    @abstractmethod
    async def set_night_vote(self, night_id: int, user_id: int, entry_id: int) -> None:
        """Голос за кандидата; upsert — один голос на человека."""

    @abstractmethod
    async def tally(self, night_id: int) -> dict[int, int]:
        """entry_id -> голосов."""


class IMovieRatingRepository(ABC):
    @abstractmethod
    async def upsert(self, entry_id: int, user_id: int, score: int, at: datetime) -> bool:
        """Ставит балл, сохраняя уже оставленный отзыв.
        True — первая оценка пользователя (для начисления очков)."""

    @abstractmethod
    async def set_review(self, entry_id: int, user_id: int, review: str, at: datetime) -> bool:
        """Пишет текстовый отзыв, сохраняя уже выставленный балл.
        True — отзыв добавлен впервые (раньше у пользователя его не было)."""

    @abstractmethod
    async def stats(self, entry_id: int) -> tuple[float | None, int]:
        """(средний балл, число выставленных баллов — отзывы без цифры не в счёт)."""

    @abstractmethod
    async def review_count(self, entry_id: int) -> int:
        """Сколько текстовых отзывов оставлено к фильму."""

    @abstractmethod
    async def list_reviews(self, entry_id: int) -> list[tuple[int, int | None, str]]:
        """Отзывы к фильму: (user_id, балл-или-None, текст), по времени."""

    @abstractmethod
    async def list_ratings(self, entry_id: int) -> list[tuple[int, int | None, str]]:
        """Все, кто оценил или написал: (user_id, балл-или-None, текст-или-''),
        по времени. Для публикации итогов в форум."""

    @abstractmethod
    async def user_stats(self, guild_id: int, user_id: int) -> tuple[int, float | None]:
        """(сколько оценок поставил, его средняя оценка) в рамках гильдии."""
