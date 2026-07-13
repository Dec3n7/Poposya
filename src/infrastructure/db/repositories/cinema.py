import json
from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.cinema.entities import MovieEntry, MovieNight
from src.domain.cinema.repository import (
    IMovieEntryRepository,
    IMovieNightRepository,
    IMovieRatingRepository,
)
from src.infrastructure.db.models.cinema import (
    MovieEntryModel,
    MovieNightModel,
    MovieNightVoteModel,
    MovieRatingModel,
    MovieVoteModel,
)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _naive(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=None) if value is not None else None


def _entry_to_domain(row: MovieEntryModel) -> MovieEntry:
    return MovieEntry(
        id=row.id,
        guild_id=row.guild_id,
        title=row.title,
        added_by=row.added_by,
        added_at=_aware(row.added_at),
        tmdb_id=row.tmdb_id,
        year=row.year,
        overview=row.overview,
        poster_url=row.poster_url,
        message_id=row.message_id,
        channel_id=row.channel_id,
        status=row.status,
        rating_message_id=row.rating_message_id,
        rating_ends_at=_aware(row.rating_ends_at),
        avg_score=row.avg_score,
        ratings_count=row.ratings_count,
        poposya_score=row.poposya_score,
        poposya_review=row.poposya_review,
        watched_at=_aware(row.watched_at),
    )


def _apply_entry(row: MovieEntryModel, entry: MovieEntry) -> None:
    row.guild_id = entry.guild_id
    row.title = entry.title
    row.added_by = entry.added_by
    row.added_at = _naive(entry.added_at)
    row.tmdb_id = entry.tmdb_id
    row.year = entry.year
    row.overview = entry.overview
    row.poster_url = entry.poster_url
    row.message_id = entry.message_id
    row.channel_id = entry.channel_id
    row.status = entry.status
    row.rating_message_id = entry.rating_message_id
    row.rating_ends_at = _naive(entry.rating_ends_at)
    row.avg_score = entry.avg_score
    row.ratings_count = entry.ratings_count
    row.poposya_score = entry.poposya_score
    row.poposya_review = entry.poposya_review
    row.watched_at = _naive(entry.watched_at)


class SqlAlchemyMovieEntryRepository(IMovieEntryRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, entry: MovieEntry) -> MovieEntry:
        row = MovieEntryModel()
        _apply_entry(row, entry)
        self._session.add(row)
        await self._session.flush()
        entry.id = row.id
        return entry

    async def save(self, entry: MovieEntry) -> None:
        if entry.id is None:
            await self.add(entry)
            return
        row = await self._session.get(MovieEntryModel, entry.id)
        if row is not None:
            _apply_entry(row, entry)

    async def get(self, entry_id: int) -> MovieEntry | None:
        row = await self._session.get(MovieEntryModel, entry_id)
        return _entry_to_domain(row) if row else None

    async def _one_where(self, *conditions) -> MovieEntry | None:
        stmt = select(MovieEntryModel).where(*conditions).limit(1)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _entry_to_domain(row) if row else None

    async def get_by_message(self, message_id: int) -> MovieEntry | None:
        return await self._one_where(MovieEntryModel.message_id == message_id)

    async def get_by_rating_message(self, message_id: int) -> MovieEntry | None:
        return await self._one_where(MovieEntryModel.rating_message_id == message_id)

    async def find_listed_duplicate(
        self, guild_id: int, tmdb_id: int | None, title_lower: str
    ) -> MovieEntry | None:
        conditions = [
            MovieEntryModel.guild_id == guild_id,
            MovieEntryModel.status == "listed",
        ]
        if tmdb_id is not None:
            conditions.append(MovieEntryModel.tmdb_id == tmdb_id)
        else:
            conditions.append(func.lower(MovieEntryModel.title) == title_lower)
        return await self._one_where(*conditions)

    async def count_listed(self, guild_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(MovieEntryModel)
            .where(
                MovieEntryModel.guild_id == guild_id,
                MovieEntryModel.status == "listed",
            )
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def count_proposed(self, guild_id: int, user_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(MovieEntryModel)
            .where(
                MovieEntryModel.guild_id == guild_id,
                MovieEntryModel.added_by == user_id,
            )
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def list_ranked(self, guild_id: int) -> list[tuple[MovieEntry, int, int]]:
        stmt = select(MovieEntryModel).where(
            MovieEntryModel.guild_id == guild_id,
            MovieEntryModel.status == "listed",
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        result: list[tuple[MovieEntry, int, int]] = []
        for row in rows:
            up, down = await self.vote_counts(row.id)
            result.append((_entry_to_domain(row), up, down))
        # чистый счёт, затем более ранние — выше
        result.sort(key=lambda item: (-(item[1] - item[2]), item[0].added_at))
        return result

    async def list_watched(self, guild_id: int) -> list[MovieEntry]:
        stmt = (
            select(MovieEntryModel)
            .where(
                MovieEntryModel.guild_id == guild_id,
                MovieEntryModel.status == "watched",
            )
            .order_by(MovieEntryModel.avg_score.desc().nulls_last())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_entry_to_domain(row) for row in rows]

    async def list_rating_pending(self) -> list[MovieEntry]:
        stmt = select(MovieEntryModel).where(MovieEntryModel.status == "rating")
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_entry_to_domain(row) for row in rows]

    async def delete(self, entry_id: int) -> bool:
        result = await self._session.execute(
            delete(MovieEntryModel).where(MovieEntryModel.id == entry_id)
        )
        await self._session.execute(
            delete(MovieVoteModel).where(MovieVoteModel.entry_id == entry_id)
        )
        return result.rowcount > 0

    async def get_vote(self, entry_id: int, user_id: int) -> int | None:
        stmt = select(MovieVoteModel.value).where(
            MovieVoteModel.entry_id == entry_id,
            MovieVoteModel.user_id == user_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def set_vote(self, entry_id: int, user_id: int, value: int) -> None:
        stmt = select(MovieVoteModel).where(
            MovieVoteModel.entry_id == entry_id,
            MovieVoteModel.user_id == user_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            self._session.add(MovieVoteModel(entry_id=entry_id, user_id=user_id, value=value))
        else:
            row.value = value

    async def remove_vote(self, entry_id: int, user_id: int) -> None:
        await self._session.execute(
            delete(MovieVoteModel).where(
                MovieVoteModel.entry_id == entry_id,
                MovieVoteModel.user_id == user_id,
            )
        )

    async def vote_counts(self, entry_id: int) -> tuple[int, int]:
        stmt = (
            select(MovieVoteModel.value, func.count())
            .where(MovieVoteModel.entry_id == entry_id)
            .group_by(MovieVoteModel.value)
        )
        counts = dict((await self._session.execute(stmt)).all())
        return counts.get(1, 0), counts.get(-1, 0)


def _night_to_domain(row: MovieNightModel) -> MovieNight:
    return MovieNight(
        id=row.id,
        guild_id=row.guild_id,
        created_by=row.created_by,
        scheduled_at=_aware(row.scheduled_at),
        poll_ends_at=_aware(row.poll_ends_at),
        candidate_ids=json.loads(row.candidates_json),
        status=row.status,
        channel_id=row.channel_id,
        poll_message_id=row.poll_message_id,
        winner_message_id=row.winner_message_id,
        winner_entry_id=row.winner_entry_id,
    )


def _apply_night(row: MovieNightModel, night: MovieNight) -> None:
    row.guild_id = night.guild_id
    row.created_by = night.created_by
    row.scheduled_at = _naive(night.scheduled_at)
    row.poll_ends_at = _naive(night.poll_ends_at)
    row.candidates_json = json.dumps(night.candidate_ids)
    row.status = night.status
    row.channel_id = night.channel_id
    row.poll_message_id = night.poll_message_id
    row.winner_message_id = night.winner_message_id
    row.winner_entry_id = night.winner_entry_id


class SqlAlchemyMovieNightRepository(IMovieNightRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, night: MovieNight) -> MovieNight:
        row = MovieNightModel()
        _apply_night(row, night)
        self._session.add(row)
        await self._session.flush()
        night.id = row.id
        return night

    async def save(self, night: MovieNight) -> None:
        if night.id is None:
            await self.add(night)
            return
        row = await self._session.get(MovieNightModel, night.id)
        if row is not None:
            _apply_night(row, night)

    async def get(self, night_id: int) -> MovieNight | None:
        row = await self._session.get(MovieNightModel, night_id)
        return _night_to_domain(row) if row else None

    async def _one_where(self, *conditions) -> MovieNight | None:
        stmt = select(MovieNightModel).where(*conditions).limit(1)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _night_to_domain(row) if row else None

    async def get_by_poll_message(self, message_id: int) -> MovieNight | None:
        return await self._one_where(MovieNightModel.poll_message_id == message_id)

    async def get_by_winner_message(self, message_id: int) -> MovieNight | None:
        return await self._one_where(MovieNightModel.winner_message_id == message_id)

    async def get_active(self, guild_id: int) -> MovieNight | None:
        return await self._one_where(
            MovieNightModel.guild_id == guild_id,
            MovieNightModel.status.in_(("poll", "scheduled")),
        )

    async def list_pending(self) -> list[MovieNight]:
        stmt = select(MovieNightModel).where(MovieNightModel.status.in_(("poll", "scheduled")))
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_night_to_domain(row) for row in rows]

    async def finish_by_entry(self, entry_id: int) -> None:
        stmt = select(MovieNightModel).where(
            MovieNightModel.winner_entry_id == entry_id,
            MovieNightModel.status == "scheduled",
        )
        for row in (await self._session.execute(stmt)).scalars().all():
            row.status = "done"

    async def set_night_vote(self, night_id: int, user_id: int, entry_id: int) -> None:
        stmt = select(MovieNightVoteModel).where(
            MovieNightVoteModel.night_id == night_id,
            MovieNightVoteModel.user_id == user_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            self._session.add(
                MovieNightVoteModel(night_id=night_id, user_id=user_id, entry_id=entry_id)
            )
        else:
            row.entry_id = entry_id

    async def tally(self, night_id: int) -> dict[int, int]:
        stmt = (
            select(MovieNightVoteModel.entry_id, func.count())
            .where(MovieNightVoteModel.night_id == night_id)
            .group_by(MovieNightVoteModel.entry_id)
        )
        return dict((await self._session.execute(stmt)).all())


class SqlAlchemyMovieRatingRepository(IMovieRatingRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _get_row(self, entry_id: int, user_id: int) -> MovieRatingModel | None:
        stmt = select(MovieRatingModel).where(
            MovieRatingModel.entry_id == entry_id,
            MovieRatingModel.user_id == user_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert(self, entry_id: int, user_id: int, score: int, at: datetime) -> bool:
        row = await self._get_row(entry_id, user_id)
        if row is None:
            self._session.add(
                MovieRatingModel(
                    entry_id=entry_id,
                    user_id=user_id,
                    score=score,
                    rated_at=_naive(at),
                )
            )
            return True
        first_time = row.score is None  # раньше был только отзыв — балл впервые
        row.score = score
        row.rated_at = _naive(at)
        return first_time

    async def set_review(self, entry_id: int, user_id: int, review: str, at: datetime) -> bool:
        row = await self._get_row(entry_id, user_id)
        if row is None:
            self._session.add(
                MovieRatingModel(
                    entry_id=entry_id,
                    user_id=user_id,
                    score=None,
                    review=review,
                    rated_at=_naive(at),
                )
            )
            return True
        first_time = not (row.review or "").strip()
        row.review = review
        row.rated_at = _naive(at)
        return first_time

    async def stats(self, entry_id: int) -> tuple[float | None, int]:
        # count(score) не считает строки-отзывы без цифры
        stmt = select(func.avg(MovieRatingModel.score), func.count(MovieRatingModel.score)).where(
            MovieRatingModel.entry_id == entry_id
        )
        avg, count = (await self._session.execute(stmt)).one()
        return (round(float(avg), 1) if avg is not None else None), count

    async def review_count(self, entry_id: int) -> int:
        stmt = select(func.count(MovieRatingModel.review)).where(
            MovieRatingModel.entry_id == entry_id
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def list_reviews(self, entry_id: int) -> list[tuple[int, int | None, str]]:
        stmt = (
            select(MovieRatingModel.user_id, MovieRatingModel.score, MovieRatingModel.review)
            .where(
                MovieRatingModel.entry_id == entry_id,
                MovieRatingModel.review.is_not(None),
            )
            .order_by(MovieRatingModel.rated_at)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            (user_id, score, review) for user_id, score, review in rows if (review or "").strip()
        ]

    async def list_ratings(self, entry_id: int) -> list[tuple[int, int | None, str]]:
        stmt = (
            select(MovieRatingModel.user_id, MovieRatingModel.score, MovieRatingModel.review)
            .where(
                MovieRatingModel.entry_id == entry_id,
                or_(
                    MovieRatingModel.score.is_not(None),
                    MovieRatingModel.review.is_not(None),
                ),
            )
            .order_by(MovieRatingModel.rated_at)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(user_id, score, (review or "")) for user_id, score, review in rows]

    async def user_stats(self, guild_id: int, user_id: int) -> tuple[int, float | None]:
        stmt = (
            select(func.count(), func.avg(MovieRatingModel.score))
            .select_from(MovieRatingModel)
            .join(MovieEntryModel, MovieEntryModel.id == MovieRatingModel.entry_id)
            .where(
                MovieEntryModel.guild_id == guild_id,
                MovieRatingModel.user_id == user_id,
            )
        )
        count, avg = (await self._session.execute(stmt)).one()
        return count, (round(float(avg), 1) if avg is not None else None)
