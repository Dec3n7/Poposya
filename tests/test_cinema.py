import random
from datetime import datetime, timedelta, timezone

import pytest

from src.application.cinema.use_cases import (
    AddMovieUseCase,
    CloseNightPollUseCase,
    FinalizeRatingUseCase,
    OpenRatingUseCase,
    RateMovieUseCase,
    StartMovieNightUseCase,
    VoteMovieUseCase,
    VoteNightUseCase,
)
from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.cinema.entities import MovieEntry, MovieNight
from src.domain.cinema.repository import (
    IMovieEntryRepository,
    IMovieNightRepository,
    IMovieRatingRepository,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def make_entry(title: str, guild_id: int = 10, tmdb_id: int | None = None) -> MovieEntry:
    return MovieEntry(
        guild_id=guild_id,
        title=title,
        added_by=1,
        added_at=NOW,
        tmdb_id=tmdb_id,
    )


class FakeMovies(IMovieEntryRepository):
    def __init__(self):
        self.rows: dict[int, MovieEntry] = {}
        self.votes: dict[tuple[int, int], int] = {}
        self._seq = 0

    async def add(self, entry):
        self._seq += 1
        entry.id = self._seq
        self.rows[entry.id] = entry
        return entry

    async def save(self, entry):
        self.rows[entry.id] = entry

    async def get(self, entry_id):
        return self.rows.get(entry_id)

    async def get_by_message(self, message_id):
        return next((e for e in self.rows.values() if e.message_id == message_id), None)

    async def get_by_rating_message(self, message_id):
        return next((e for e in self.rows.values() if e.rating_message_id == message_id), None)

    async def find_listed_duplicate(self, guild_id, tmdb_id, title_lower):
        for e in self.rows.values():
            if e.guild_id != guild_id or e.status != "listed":
                continue
            if tmdb_id is not None and e.tmdb_id == tmdb_id:
                return e
            if tmdb_id is None and e.title.lower() == title_lower:
                return e
        return None

    async def count_listed(self, guild_id):
        return len(
            [e for e in self.rows.values() if e.guild_id == guild_id and e.status == "listed"]
        )

    async def count_proposed(self, guild_id, user_id):
        return len(
            [e for e in self.rows.values() if e.guild_id == guild_id and e.added_by == user_id]
        )

    async def list_ranked(self, guild_id):
        result = []
        for e in self.rows.values():
            if e.guild_id == guild_id and e.status == "listed":
                up, down = await self.vote_counts(e.id)
                result.append((e, up, down))
        result.sort(key=lambda item: (-(item[1] - item[2]), item[0].added_at))
        return result

    async def list_watched(self, guild_id):
        rows = [e for e in self.rows.values() if e.guild_id == guild_id and e.status == "watched"]
        return sorted(rows, key=lambda e: e.avg_score or 0, reverse=True)

    async def list_rating_pending(self):
        return [e for e in self.rows.values() if e.status == "rating"]

    async def delete(self, entry_id):
        return self.rows.pop(entry_id, None) is not None

    async def get_vote(self, entry_id, user_id):
        return self.votes.get((entry_id, user_id))

    async def set_vote(self, entry_id, user_id, value):
        self.votes[(entry_id, user_id)] = value

    async def remove_vote(self, entry_id, user_id):
        self.votes.pop((entry_id, user_id), None)

    async def vote_counts(self, entry_id):
        values = [v for (eid, _), v in self.votes.items() if eid == entry_id]
        return values.count(1), values.count(-1)


class FakeNights(IMovieNightRepository):
    def __init__(self):
        self.rows: dict[int, MovieNight] = {}
        self.votes: dict[tuple[int, int], int] = {}  # (night, user) -> entry
        self._seq = 0

    async def add(self, night):
        self._seq += 1
        night.id = self._seq
        self.rows[night.id] = night
        return night

    async def save(self, night):
        self.rows[night.id] = night

    async def get(self, night_id):
        return self.rows.get(night_id)

    async def get_by_poll_message(self, message_id):
        return next((n for n in self.rows.values() if n.poll_message_id == message_id), None)

    async def get_by_winner_message(self, message_id):
        return next((n for n in self.rows.values() if n.winner_message_id == message_id), None)

    async def get_active(self, guild_id):
        return next(
            (
                n
                for n in self.rows.values()
                if n.guild_id == guild_id and n.status in ("poll", "scheduled")
            ),
            None,
        )

    async def list_pending(self):
        return [n for n in self.rows.values() if n.status in ("poll", "scheduled")]

    async def finish_by_entry(self, entry_id):
        for n in self.rows.values():
            if n.winner_entry_id == entry_id and n.status == "scheduled":
                n.status = "done"

    async def set_night_vote(self, night_id, user_id, entry_id):
        self.votes[(night_id, user_id)] = entry_id

    async def tally(self, night_id):
        result: dict[int, int] = {}
        for (nid, _), eid in self.votes.items():
            if nid == night_id:
                result[eid] = result.get(eid, 0) + 1
        return result


class FakeRatings(IMovieRatingRepository):
    def __init__(self):
        self.scores: dict[tuple[int, int], int] = {}
        self.reviews: dict[tuple[int, int], str] = {}

    async def upsert(self, entry_id, user_id, score, at):
        first = self.scores.get((entry_id, user_id)) is None
        self.scores[(entry_id, user_id)] = score
        return first

    async def set_review(self, entry_id, user_id, review, at):
        first = not (self.reviews.get((entry_id, user_id)) or "")
        self.reviews[(entry_id, user_id)] = review
        return first

    async def stats(self, entry_id):
        values = [s for (eid, _), s in self.scores.items() if eid == entry_id and s is not None]
        if not values:
            return None, 0
        return round(sum(values) / len(values), 1), len(values)

    async def review_count(self, entry_id):
        return sum(1 for (eid, _), r in self.reviews.items() if eid == entry_id and r)

    async def list_reviews(self, entry_id):
        return [
            (uid, self.scores.get((eid, uid)), r)
            for (eid, uid), r in self.reviews.items()
            if eid == entry_id and r
        ]

    async def list_ratings(self, entry_id):
        users = {uid for (eid, uid) in self.scores if eid == entry_id} | {
            uid for (eid, uid) in self.reviews if eid == entry_id
        }
        return [
            (uid, self.scores.get((entry_id, uid)), self.reviews.get((entry_id, uid), ""))
            for uid in users
        ]

    def bind_entries(self, movies: "FakeMovies"):
        self._movies = movies
        return self

    async def user_stats(self, guild_id, user_id):
        values = [
            s
            for (eid, uid), s in self.scores.items()
            if uid == user_id
            and eid in self._movies.rows
            and self._movies.rows[eid].guild_id == guild_id
        ]
        if not values:
            return 0, None
        return len(values), round(sum(values) / len(values), 1)


class FakeUoW(IUnitOfWork):
    def __init__(self, state):
        self.movies = state["movies"]
        self.movie_nights = state["nights"]
        self.movie_ratings = state["ratings"]

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    def add_event(self, event):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.fixture
def state():
    movies = FakeMovies()
    return {
        "movies": movies,
        "nights": FakeNights(),
        "ratings": FakeRatings().bind_entries(movies),
    }


@pytest.fixture
def uow_factory(state):
    return lambda: FakeUoW(state)


# --- провайдеры поиска ---


async def test_fallback_movie_search():
    from src.infrastructure.cinema.provider import FallbackMovieSearch, MovieInfo

    info = MovieInfo(tmdb_id=1, title="Akira", year=1988, overview="", poster_url="")

    class Dead:
        enabled = True

        async def search(self, query, limit=5):
            raise TimeoutError("blocked by ip")

    class Empty:
        enabled = True

        async def search(self, query, limit=5):
            return []

    class Alive:
        enabled = True

        async def search(self, query, limit=5):
            return [info]

    class Disabled:
        enabled = False

        async def search(self, query, limit=5):
            raise AssertionError("выключенный провайдер не должен вызываться")

    # основной упал -> резерв спасает
    assert await FallbackMovieSearch(Dead(), Alive()).search("akira") == [info]
    # основной пуст -> пробуем резерв
    assert await FallbackMovieSearch(Empty(), Alive()).search("akira") == [info]
    # выключенный пропускается, оба мертвы -> пусто
    assert await FallbackMovieSearch(Disabled(), Empty()).search("akira") == []
    assert not FallbackMovieSearch(Disabled(), Disabled()).enabled


# --- вотчлист ---


async def test_add_movie_duplicates_and_limit(uow_factory):
    uc = AddMovieUseCase(uow_factory, watchlist_max=2)
    assert (await uc.execute(make_entry("Akira", tmdb_id=100))).status == "ok"
    # дубликат по tmdb_id
    assert (await uc.execute(make_entry("Акира", tmdb_id=100))).status == "duplicate"
    # дубликат по названию без tmdb_id
    await uc.execute(make_entry("Paprika"))
    assert (await uc.execute(make_entry("paprika"))).status == "duplicate"
    # лимит
    assert (await uc.execute(make_entry("Ghost in the Shell"))).status == "limit"


async def test_vote_toggle_and_switch(state, uow_factory):
    entry = await state["movies"].add(make_entry("Akira"))
    entry.message_id = 500
    uc = VoteMovieUseCase(uow_factory)
    result = await uc.execute(500, 1, +1)
    assert (result.up, result.down, result.my_vote) == (1, 0, 1)
    # смена голоса
    result = await uc.execute(500, 1, -1)
    assert (result.up, result.down, result.my_vote) == (0, 1, -1)
    # повтор той же кнопки — снятие
    result = await uc.execute(500, 1, -1)
    assert (result.up, result.down, result.my_vote) == (0, 0, None)
    # чужое сообщение
    assert (await uc.execute(999, 1, +1)).status == "gone"


# --- киновечер ---


async def _fill_watchlist(state, count=3):
    entries = []
    for i in range(count):
        entries.append(await state["movies"].add(make_entry(f"Movie {i}")))
    return entries


async def test_night_lifecycle(state, uow_factory):
    entries = await _fill_watchlist(state)
    start = StartMovieNightUseCase(uow_factory, poll_options=5)
    scheduled = NOW + timedelta(hours=6)
    result = await start.execute(10, 1, scheduled, NOW)
    assert result.status == "ok"
    night = result.night
    assert [e.id for e in result.candidates] == [e.id for e in entries]
    assert night.poll_ends_at == scheduled - timedelta(hours=1)
    # второй вечер параллельно не устроить
    assert (await start.execute(10, 2, scheduled, NOW)).status == "busy"
    # в пустой гильдии — empty
    assert (await start.execute(99, 1, scheduled, NOW)).status == "empty"

    night.poll_message_id = 700
    vote = VoteNightUseCase(uow_factory)
    assert await vote.execute(700, 1, entries[1].id, NOW) == "ok"
    assert await vote.execute(700, 2, entries[1].id, NOW) == "ok"
    assert await vote.execute(700, 3, entries[0].id, NOW) == "ok"
    # после дедлайна голос не принимается
    assert await vote.execute(700, 4, entries[0].id, night.poll_ends_at) == "closed"

    close = CloseNightPollUseCase(uow_factory, rng=random.Random(1))
    result = await close.execute(night.id)
    assert result.status == "winner"
    assert result.winner.id == entries[1].id
    assert result.night.status == "scheduled"


async def test_poll_without_votes_cancels(state, uow_factory):
    await _fill_watchlist(state, 1)
    start = StartMovieNightUseCase(uow_factory, poll_options=5)
    night = (await start.execute(10, 1, NOW + timedelta(hours=6), NOW)).night
    close = CloseNightPollUseCase(uow_factory)
    result = await close.execute(night.id)
    assert result.status == "no_votes"
    assert night.status == "cancelled"
    # после отмены можно устроить новый вечер
    assert (await start.execute(10, 1, NOW + timedelta(hours=6), NOW)).status == "ok"


# --- оценки ---


async def test_rating_flow(state, uow_factory):
    entry = await state["movies"].add(make_entry("Akira"))
    open_uc = OpenRatingUseCase(uow_factory, rating_hours=24)
    opened = await open_uc.execute(entry.id, NOW)
    assert opened.status == "rating"
    assert opened.rating_ends_at == NOW + timedelta(hours=24)
    # второй раз не открыть
    assert await open_uc.execute(entry.id, NOW) is None

    entry.rating_message_id = 800
    rate = RateMovieUseCase(uow_factory)
    first = await rate.execute(800, 1, 8, NOW)
    assert first.status == "ok" and first.first_time and first.count == 1
    # смена оценки — не первая
    second = await rate.execute(800, 1, 9, NOW)
    assert not second.first_time and second.count == 1
    await rate.execute(800, 2, 6, NOW)

    finalize = FinalizeRatingUseCase(uow_factory)
    result = await finalize.execute(entry.id, NOW, poposya_score=7, poposya_review="7/10 — сойдёт.")
    assert result.avg == 7.5 and result.count == 2
    assert result.entry.status == "watched"
    assert result.entry.poposya_score == 7
    # закрытый сбор не принимает оценок
    assert (await rate.execute(800, 3, 10, NOW)).status == "closed"


async def test_cinema_profile_stats(state, uow_factory):
    from src.application.cinema.use_cases import GetCinemaProfileUseCase

    entry = await state["movies"].add(make_entry("Akira"))
    other = await state["movies"].add(make_entry("Paprika"))
    await state["ratings"].upsert(entry.id, 1, 8, NOW)
    await state["ratings"].upsert(other.id, 1, 6, NOW)
    uc = GetCinemaProfileUseCase(uow_factory)
    profile = await uc.execute(10, 1)
    assert profile.proposed == 2  # make_entry ставит added_by=1
    assert profile.ratings_count == 2
    assert profile.avg_given == 7.0
    empty = await uc.execute(10, 99)
    assert (empty.proposed, empty.ratings_count, empty.avg_given) == (0, 0, None)


async def test_rating_via_winner_message(state, uow_factory):
    entries = await _fill_watchlist(state, 1)
    night = await state["nights"].add(
        MovieNight(
            guild_id=10,
            created_by=1,
            scheduled_at=NOW,
            poll_ends_at=NOW,
            status="scheduled",
            winner_entry_id=entries[0].id,
            winner_message_id=900,
        )
    )
    open_uc = OpenRatingUseCase(uow_factory, rating_hours=24)
    opened = await open_uc.execute_by_winner_message(900, NOW)
    assert opened is not None and opened.id == entries[0].id
    # ночь завершается при финализации
    entries[0].rating_message_id = 901
    rate = RateMovieUseCase(uow_factory)
    await rate.execute(901, 1, 10, NOW)
    finalize = FinalizeRatingUseCase(uow_factory)
    await finalize.execute(entries[0].id, NOW)
    assert night.status == "done"
