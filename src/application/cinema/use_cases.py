import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.cinema.entities import MovieEntry, MovieNight

UowFactory = Callable[[], IUnitOfWork]


@dataclass(frozen=True)
class AddMovieResult:
    # ok | duplicate | limit
    status: str
    entry: MovieEntry | None = None


class AddMovieUseCase:
    def __init__(self, uow_factory: UowFactory, watchlist_max: int, settings_provider=None):
        self._uow_factory = uow_factory
        self._watchlist_max = watchlist_max
        self._settings = settings_provider

    async def execute(self, entry: MovieEntry) -> AddMovieResult:
        watchlist_max = (
            self._settings.get(entry.guild_id, "cinema_watchlist_max", self._watchlist_max)
            if self._settings is not None
            else self._watchlist_max
        )
        async with self._uow_factory() as uow:
            duplicate = await uow.movies.find_listed_duplicate(
                entry.guild_id, entry.tmdb_id, entry.title.strip().lower()
            )
            if duplicate is not None:
                return AddMovieResult(status="duplicate", entry=duplicate)
            if await uow.movies.count_listed(entry.guild_id) >= watchlist_max:
                return AddMovieResult(status="limit")
            saved = await uow.movies.add(entry)
            await uow.commit()
            return AddMovieResult(status="ok", entry=saved)


class RegisterMovieMessageUseCase:
    """Привязка Discord-сообщений к сущностям (карточка/опрос/победитель/оценки)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, kind: str, target_id: int, channel_id: int, message_id: int) -> None:
        async with self._uow_factory() as uow:
            if kind in ("card", "rating"):
                entry = await uow.movies.get(target_id)
                if entry is None:
                    return
                entry.channel_id = channel_id
                if kind == "card":
                    entry.message_id = message_id
                else:
                    entry.rating_message_id = message_id
                await uow.movies.save(entry)
            else:  # poll | winner
                night = await uow.movie_nights.get(target_id)
                if night is None:
                    return
                night.channel_id = channel_id
                if kind == "poll":
                    night.poll_message_id = message_id
                else:
                    night.winner_message_id = message_id
                await uow.movie_nights.save(night)
            await uow.commit()


@dataclass(frozen=True)
class VoteResult:
    # ok | gone
    status: str
    up: int = 0
    down: int = 0
    my_vote: int | None = None  # итоговый голос нажавшего (None = снят)


class VoteMovieUseCase:
    """👍/👎 на карточке: повторное нажатие той же кнопки снимает голос,
    другой кнопки — меняет его."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, message_id: int, user_id: int, value: int) -> VoteResult:
        async with self._uow_factory() as uow:
            entry = await uow.movies.get_by_message(message_id)
            if entry is None or entry.id is None or entry.status != "listed":
                return VoteResult(status="gone")
            current = await uow.movies.get_vote(entry.id, user_id)
            if current == value:
                await uow.movies.remove_vote(entry.id, user_id)
                my_vote = None
            else:
                await uow.movies.set_vote(entry.id, user_id, value)
                my_vote = value
            up, down = await uow.movies.vote_counts(entry.id)
            await uow.commit()
            return VoteResult(status="ok", up=up, down=down, my_vote=my_vote)


class ListWatchlistUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int) -> list[tuple[MovieEntry, int, int]]:
        async with self._uow_factory() as uow:
            return await uow.movies.list_ranked(guild_id)


class TopWatchedUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int) -> list[MovieEntry]:
        async with self._uow_factory() as uow:
            return await uow.movies.list_watched(guild_id)


class RemoveMovieUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self, guild_id: int, entry_id: int, user_id: int, is_admin: bool
    ) -> tuple[str, MovieEntry | None]:
        """ok | not_found | forbidden — убрать может добавивший или админ."""
        async with self._uow_factory() as uow:
            entry = await uow.movies.get(entry_id)
            if entry is None or entry.guild_id != guild_id or entry.status != "listed":
                return "not_found", None
            if entry.added_by != user_id and not is_admin:
                return "forbidden", None
            await uow.movies.delete(entry_id)
            await uow.commit()
            return "ok", entry


@dataclass(frozen=True)
class StartNightResult:
    # ok | busy | empty
    status: str
    night: MovieNight | None = None
    candidates: list[MovieEntry] | None = None


class StartMovieNightUseCase:
    """Опрос по топ-N вотчлиста. Голосование закрывается за час до сеанса
    (но не позже, чем через сутки, и не раньше, чем через 10 минут)."""

    def __init__(self, uow_factory: UowFactory, poll_options: int):
        self._uow_factory = uow_factory
        self._poll_options = poll_options

    async def execute(
        self, guild_id: int, created_by: int, scheduled_at: datetime, now: datetime
    ) -> StartNightResult:
        async with self._uow_factory() as uow:
            if await uow.movie_nights.get_active(guild_id) is not None:
                return StartNightResult(status="busy")
            ranked = await uow.movies.list_ranked(guild_id)
            if not ranked:
                return StartNightResult(status="empty")
            candidates = [entry for entry, _, _ in ranked[: self._poll_options]]
            poll_ends = max(
                now + timedelta(minutes=10),
                min(scheduled_at - timedelta(hours=1), now + timedelta(hours=24)),
            )
            night = await uow.movie_nights.add(
                MovieNight(
                    guild_id=guild_id,
                    created_by=created_by,
                    scheduled_at=scheduled_at,
                    poll_ends_at=poll_ends,
                    candidate_ids=[e.id for e in candidates if e.id is not None],
                )
            )
            await uow.commit()
            return StartNightResult(status="ok", night=night, candidates=candidates)


class VoteNightUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self, poll_message_id: int, user_id: int, entry_id: int, now: datetime
    ) -> str:
        """ok | closed."""
        async with self._uow_factory() as uow:
            night = await uow.movie_nights.get_by_poll_message(poll_message_id)
            if (
                night is None
                or night.id is None
                or night.status != "poll"
                or now >= night.poll_ends_at
                or entry_id not in night.candidate_ids
            ):
                return "closed"
            await uow.movie_nights.set_night_vote(night.id, user_id, entry_id)
            await uow.commit()
            return "ok"


@dataclass(frozen=True)
class ClosePollResult:
    # winner | no_votes | gone
    status: str
    night: MovieNight | None = None
    winner: MovieEntry | None = None
    votes: dict[int, int] | None = None


class CloseNightPollUseCase:
    """Подводит итог опроса. Ничья решается чистым счётом вотчлиста,
    затем случайно. Без голосов ночь отменяется."""

    def __init__(self, uow_factory: UowFactory, rng: random.Random | None = None):
        self._uow_factory = uow_factory
        self._rng = rng or random.Random()

    async def execute(self, night_id: int) -> ClosePollResult:
        async with self._uow_factory() as uow:
            night = await uow.movie_nights.get(night_id)
            if night is None or night.status != "poll":
                return ClosePollResult(status="gone")
            votes = await uow.movie_nights.tally(night_id)
            if not votes:
                night.status = "cancelled"
                await uow.movie_nights.save(night)
                await uow.commit()
                return ClosePollResult(status="no_votes", night=night)
            best = max(votes.values())
            top_ids = [eid for eid, count in votes.items() if count == best]
            if len(top_ids) > 1:
                ranked = await uow.movies.list_ranked(night.guild_id)
                net = {entry.id: up - down for entry, up, down in ranked}
                best_net = max(net.get(eid, 0) for eid in top_ids)
                top_ids = [eid for eid in top_ids if net.get(eid, 0) == best_net]
            winner_id = self._rng.choice(top_ids)
            winner = await uow.movies.get(winner_id)
            if winner is None:
                night.status = "cancelled"
                await uow.movie_nights.save(night)
                await uow.commit()
                return ClosePollResult(status="no_votes", night=night)
            night.status = "scheduled"
            night.winner_entry_id = winner_id
            await uow.movie_nights.save(night)
            await uow.commit()
            return ClosePollResult(status="winner", night=night, winner=winner, votes=votes)


class CancelNightUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self, guild_id: int, user_id: int, is_admin: bool
    ) -> tuple[str, MovieNight | None]:
        """ok | none | forbidden."""
        async with self._uow_factory() as uow:
            night = await uow.movie_nights.get_active(guild_id)
            if night is None:
                return "none", None
            if night.created_by != user_id and not is_admin:
                return "forbidden", None
            night.status = "cancelled"
            await uow.movie_nights.save(night)
            await uow.commit()
            return "ok", night


class OpenRatingUseCase:
    """Открывает сбор оценок 1–10 по фильму (после киновечера или
    /movie watched). Возвращает entry или None, если фильм не в вотчлисте."""

    def __init__(
        self,
        uow_factory: UowFactory,
        rating_hours: int,
        rating_minutes: int = 0,
        settings_provider=None,
    ):
        self._uow_factory = uow_factory
        self._rating_hours = rating_hours
        # rating_minutes > 0 переопределяет часы глобально (короткое окно для тестов)
        self._rating_minutes = rating_minutes
        self._settings = settings_provider

    def _window(self, guild_id: int) -> timedelta:
        if self._rating_minutes > 0:
            return timedelta(minutes=self._rating_minutes)
        hours = (
            self._settings.get(guild_id, "cinema_rating_hours", self._rating_hours)
            if self._settings is not None
            else self._rating_hours
        )
        return timedelta(hours=hours)

    async def execute(self, entry_id: int, now: datetime) -> MovieEntry | None:
        async with self._uow_factory() as uow:
            entry = await uow.movies.get(entry_id)
            if entry is None or entry.status != "listed":
                return None
            entry.status = "rating"
            entry.rating_ends_at = now + self._window(entry.guild_id)
            await uow.movies.save(entry)
            await uow.commit()
            return entry

    async def execute_by_winner_message(self, message_id: int, now: datetime) -> MovieEntry | None:
        """Кнопка «Мы посмотрели» под анонсом победителя киновечера."""
        async with self._uow_factory() as uow:
            night = await uow.movie_nights.get_by_winner_message(message_id)
        if night is None or night.status != "scheduled" or night.winner_entry_id is None:
            return None
        return await self.execute(night.winner_entry_id, now)


@dataclass(frozen=True)
class RateResult:
    # ok | closed
    status: str
    first_time: bool = False
    count: int = 0  # выставленных баллов
    reviews: int = 0  # текстовых отзывов


class RateMovieUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self, rating_message_id: int, user_id: int, score: int, now: datetime
    ) -> RateResult:
        async with self._uow_factory() as uow:
            entry = await uow.movies.get_by_rating_message(rating_message_id)
            if entry is None or entry.id is None or entry.status != "rating":
                return RateResult(status="closed")
            first = await uow.movie_ratings.upsert(entry.id, user_id, score, now)
            _, count = await uow.movie_ratings.stats(entry.id)
            reviews = await uow.movie_ratings.review_count(entry.id)
            await uow.commit()
            return RateResult(status="ok", first_time=first, count=count, reviews=reviews)


class ReviewMovieUseCase:
    """Текстовый отзыв зрителя (из модалки). Пишется только пока идёт сбор
    оценок; балл не требуется — отзыв самодостаточен."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self, rating_message_id: int, user_id: int, review: str, now: datetime
    ) -> RateResult:
        async with self._uow_factory() as uow:
            entry = await uow.movies.get_by_rating_message(rating_message_id)
            if entry is None or entry.id is None or entry.status != "rating":
                return RateResult(status="closed")
            first = await uow.movie_ratings.set_review(entry.id, user_id, review.strip()[:500], now)
            _, count = await uow.movie_ratings.stats(entry.id)
            reviews = await uow.movie_ratings.review_count(entry.id)
            await uow.commit()
            return RateResult(status="ok", first_time=first, count=count, reviews=reviews)


@dataclass(frozen=True)
class MovieReview:
    user_id: int
    score: int | None
    text: str


class GetMovieReviewsUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, entry_id: int) -> list[MovieReview]:
        async with self._uow_factory() as uow:
            rows = await uow.movie_ratings.list_reviews(entry_id)
            return [MovieReview(user_id=u, score=s, text=t) for u, s, t in rows]


class GetMovieRatingsUseCase:
    """Все оценившие/написавшие — для публикации итогов в форум."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, entry_id: int) -> list[MovieReview]:
        async with self._uow_factory() as uow:
            rows = await uow.movie_ratings.list_ratings(entry_id)
            return [MovieReview(user_id=u, score=s, text=t) for u, s, t in rows]


@dataclass(frozen=True)
class FinalizeResult:
    entry: MovieEntry
    avg: float | None
    count: int


class FinalizeRatingUseCase:
    """Закрывает сбор оценок: средний балл, статус watched, вердикт Попоси
    (передаёт ког), связанный киновечер — в done."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self,
        entry_id: int,
        now: datetime,
        poposya_score: int | None = None,
        poposya_review: str = "",
    ) -> FinalizeResult | None:
        async with self._uow_factory() as uow:
            entry = await uow.movies.get(entry_id)
            if entry is None or entry.status != "rating":
                return None
            avg, count = await uow.movie_ratings.stats(entry_id)
            entry.status = "watched"
            entry.avg_score = avg
            entry.ratings_count = count
            entry.watched_at = now
            entry.rating_ends_at = None
            entry.poposya_score = poposya_score
            entry.poposya_review = poposya_review[:300]
            await uow.movies.save(entry)
            await uow.movie_nights.finish_by_entry(entry_id)
            await uow.commit()
            return FinalizeResult(entry=entry, avg=avg, count=count)


@dataclass(frozen=True)
class PendingCinema:
    polls: list[MovieNight]  # идёт голосование
    scheduled: list[MovieNight]  # ждём сеанса
    ratings: list[MovieEntry]  # идёт сбор оценок


class ListPendingCinemaUseCase:
    """Все незавершённые процессы киноклуба — восстановление после рестарта."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self) -> PendingCinema:
        async with self._uow_factory() as uow:
            nights = await uow.movie_nights.list_pending()
            return PendingCinema(
                polls=[n for n in nights if n.status == "poll"],
                scheduled=[n for n in nights if n.status == "scheduled"],
                ratings=await uow.movies.list_rating_pending(),
            )


@dataclass(frozen=True)
class CinemaProfile:
    proposed: int  # сколько фильмов предложил
    ratings_count: int  # сколько оценок поставил
    avg_given: float | None  # его средняя оценка


class GetCinemaProfileUseCase:
    """Кино-статистика пользователя для профиля-витрины."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, user_id: int) -> CinemaProfile:
        async with self._uow_factory() as uow:
            proposed = await uow.movies.count_proposed(guild_id, user_id)
            count, avg = await uow.movie_ratings.user_stats(guild_id, user_id)
            return CinemaProfile(proposed=proposed, ratings_count=count, avg_given=avg)


class GetMovieUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, entry_id: int) -> MovieEntry | None:
        async with self._uow_factory() as uow:
            return await uow.movies.get(entry_id)
