"""Прямые тесты SQL-репозиториев киноклуба на SQLite: вотчлист, голоса 👍/👎,
ранжирование, киновечера с опросом, оценки 1–10 и агрегаты."""
from datetime import datetime, timedelta, timezone

import pytest

from src.domain.cinema.entities import MovieEntry, MovieNight
from src.infrastructure.db.repositories.cinema import (
    SqlAlchemyMovieEntryRepository,
    SqlAlchemyMovieNightRepository,
    SqlAlchemyMovieRatingRepository,
)

NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)


def make_entry(title, **over):
    base = dict(guild_id=10, title=title, added_by=1, added_at=NOW)
    base.update(over)
    return MovieEntry(**base)


# --- MovieEntry -------------------------------------------------------------

async def test_entry_add_get_and_save(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyMovieEntryRepository(s)
        entry = await repo.add(make_entry("Начало", tmdb_id=27205, year=2010))
        await s.commit()
        assert entry.id is not None
        loaded = await repo.get(entry.id)
        assert loaded.title == "Начало" and loaded.year == 2010

        loaded.status = "watched"
        loaded.avg_score = 8.5
        await repo.save(loaded)
        await s.commit()
        assert (await repo.get(entry.id)).status == "watched"


async def test_entry_lookup_by_message(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyMovieEntryRepository(s)
        await repo.add(make_entry("A", message_id=500, rating_message_id=600))
        await s.commit()
        assert (await repo.get_by_message(500)).title == "A"
        assert (await repo.get_by_rating_message(600)).title == "A"
        assert await repo.get_by_message(999) is None


async def test_find_listed_duplicate_by_tmdb_and_title(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyMovieEntryRepository(s)
        await repo.add(make_entry("Матрица", tmdb_id=603))
        await repo.add(make_entry("The Matrix"))  # ASCII: SQLite LOWER регистронезависим только для латиницы
        await s.commit()
        assert (await repo.find_listed_duplicate(10, 603, "матрица")).tmdb_id == 603
        # по названию (без tmdb_id), регистронезависимо
        assert (await repo.find_listed_duplicate(10, None, "the matrix")) is not None
        assert await repo.find_listed_duplicate(10, 111, "нет") is None


async def test_counts_listed_and_proposed(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyMovieEntryRepository(s)
        await repo.add(make_entry("A", added_by=1))
        await repo.add(make_entry("B", added_by=1))
        await repo.add(make_entry("C", added_by=2, status="watched"))
        await s.commit()
        assert await repo.count_listed(10) == 2  # watched не считается
        assert await repo.count_proposed(10, 1) == 2


async def test_votes_and_ranked_ordering(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyMovieEntryRepository(s)
        a = await repo.add(make_entry("A", added_at=NOW))
        b = await repo.add(make_entry("B", added_at=NOW + timedelta(minutes=1)))
        await s.commit()

        # A: +2, B: +1 -> A выше
        repo_ = repo
        await repo_.set_vote(a.id, 1, 1)
        await repo_.set_vote(a.id, 2, 1)
        await repo_.set_vote(b.id, 3, 1)
        await s.commit()

        assert await repo.get_vote(a.id, 1) == 1
        assert await repo.vote_counts(a.id) == (2, 0)

        ranked = await repo.list_ranked(10)
        assert [e.title for e, up, down in ranked] == ["A", "B"]

        # смена и снятие голоса
        await repo.set_vote(a.id, 1, -1)
        await repo.remove_vote(a.id, 2)
        await s.commit()
        assert await repo.vote_counts(a.id) == (0, 1)


async def test_delete_entry_removes_votes(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyMovieEntryRepository(s)
        e = await repo.add(make_entry("A"))
        await repo.set_vote(e.id, 1, 1)
        await s.commit()
        assert await repo.delete(e.id) is True
        await s.commit()
        assert await repo.get(e.id) is None
        assert await repo.vote_counts(e.id) == (0, 0)
        assert await repo.delete(e.id) is False  # уже нет


async def test_list_watched_and_rating_pending(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyMovieEntryRepository(s)
        await repo.add(make_entry("Low", status="watched", avg_score=5.0))
        await repo.add(make_entry("High", status="watched", avg_score=9.0))
        await repo.add(make_entry("Pending", status="rating"))
        await s.commit()
        watched = await repo.list_watched(10)
        assert [e.title for e in watched] == ["High", "Low"]  # по убыванию оценки
        pending = await repo.list_rating_pending()
        assert [e.title for e in pending] == ["Pending"]


# --- MovieNight -------------------------------------------------------------

def make_night(**over):
    base = dict(
        guild_id=10, created_by=1, scheduled_at=NOW + timedelta(days=1),
        poll_ends_at=NOW + timedelta(hours=2), candidate_ids=[1, 2, 3],
    )
    base.update(over)
    return MovieNight(**base)


async def test_night_add_get_and_active(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyMovieNightRepository(s)
        night = await repo.add(make_night(poll_message_id=700))
        await s.commit()
        assert night.id is not None
        loaded = await repo.get(night.id)
        assert loaded.candidate_ids == [1, 2, 3]
        assert (await repo.get_active(10)).id == night.id
        assert (await repo.get_by_poll_message(700)).id == night.id


async def test_night_votes_and_tally(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyMovieNightRepository(s)
        night = await repo.add(make_night())
        await s.commit()
        await repo.set_night_vote(night.id, 1, entry_id=2)
        await repo.set_night_vote(night.id, 2, entry_id=2)
        await repo.set_night_vote(night.id, 3, entry_id=1)
        await s.commit()
        tally = await repo.tally(night.id)
        assert tally == {2: 2, 1: 1}

        # смена голоса перезаписывает
        await repo.set_night_vote(night.id, 1, entry_id=1)
        await s.commit()
        assert await repo.tally(night.id) == {1: 2, 2: 1}


async def test_night_finish_by_entry(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyMovieNightRepository(s)
        night = await repo.add(make_night(status="scheduled", winner_entry_id=42))
        await s.commit()
        await repo.finish_by_entry(42)
        await s.commit()
        assert (await repo.get(night.id)).status == "done"
        assert await repo.get_active(10) is None  # done не активна


async def test_night_list_pending(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyMovieNightRepository(s)
        await repo.add(make_night(status="poll"))
        await repo.add(make_night(status="done"))
        await s.commit()
        pending = await repo.list_pending()
        assert len(pending) == 1 and pending[0].status == "poll"


# --- MovieRating ------------------------------------------------------------

async def test_rating_upsert_and_stats(session_factory):
    async with session_factory() as s:
        entries = SqlAlchemyMovieEntryRepository(s)
        entry = await entries.add(make_entry("A"))
        await s.commit()
        ratings = SqlAlchemyMovieRatingRepository(s)

        assert await ratings.upsert(entry.id, 1, 8, NOW) is True   # новая
        assert await ratings.upsert(entry.id, 2, 10, NOW) is True
        assert await ratings.upsert(entry.id, 1, 6, NOW) is False  # обновление
        await s.commit()

        avg, count = await ratings.stats(entry.id)
        assert count == 2 and avg == 8.0  # (6 + 10) / 2

        cnt, user_avg = await ratings.user_stats(10, 1)
        assert cnt == 1 and user_avg == 6.0


async def test_rating_stats_empty(session_factory):
    async with session_factory() as s:
        ratings = SqlAlchemyMovieRatingRepository(s)
        avg, count = await ratings.stats(999)
        assert avg is None and count == 0
