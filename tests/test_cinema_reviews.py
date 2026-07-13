"""Отзывы зрителей к фильмам: SQL-репозиторий (балл + текст, отзыв без балла)
и use-cases ReviewMovie / GetMovieReviews поверх реального UoW."""
from datetime import datetime, timezone

import pytest

from datetime import timedelta

from src.application.cinema.use_cases import (
    GetMovieRatingsUseCase,
    GetMovieReviewsUseCase,
    OpenRatingUseCase,
    RateMovieUseCase,
    ReviewMovieUseCase,
)
from src.domain.cinema.entities import MovieEntry
from src.infrastructure.db.repositories.cinema import (
    SqlAlchemyMovieEntryRepository,
    SqlAlchemyMovieRatingRepository,
)

NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)


def make_entry(**over):
    base = dict(guild_id=10, title="Фильм", added_by=1, added_at=NOW,
                status="rating", rating_message_id=500)
    base.update(over)
    return MovieEntry(**base)


# --- репозиторий оценок ------------------------------------------------------

async def test_review_only_without_score(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyMovieRatingRepository(s)
        assert await repo.set_review(1, 7, "отличное кино", NOW) is True
        await s.commit()
        # отзыв без цифры: в статистику баллов не попадает
        avg, count = await repo.stats(1)
        assert avg is None and count == 0
        assert await repo.review_count(1) == 1
        reviews = await repo.list_reviews(1)
        assert reviews == [(7, None, "отличное кино")]


async def test_score_and_review_merge_into_one_row(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyMovieRatingRepository(s)
        await repo.upsert(1, 7, 9, NOW)       # сначала балл
        await repo.set_review(1, 7, "9 из 10, шедевр", NOW)  # потом отзыв
        await s.commit()
        avg, count = await repo.stats(1)
        assert (avg, count) == (9.0, 1)       # балл сохранился
        assert await repo.review_count(1) == 1
        reviews = await repo.list_reviews(1)
        assert reviews == [(7, 9, "9 из 10, шедевр")]  # оба поля в одной строке


async def test_review_then_score_marks_first_score(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyMovieRatingRepository(s)
        await repo.set_review(1, 7, "мысли", NOW)
        # балл впервые (был только отзыв) -> True для начисления очков
        assert await repo.upsert(1, 7, 8, NOW) is True
        await s.commit()
        assert (await repo.stats(1)) == (8.0, 1)


async def test_stats_ignores_reviewers_without_score(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyMovieRatingRepository(s)
        await repo.upsert(1, 1, 8, NOW)          # балл
        await repo.upsert(1, 2, 6, NOW)          # балл
        await repo.set_review(1, 3, "без цифры", NOW)  # только отзыв
        await s.commit()
        avg, count = await repo.stats(1)
        assert count == 2 and avg == 7.0         # третий не учтён в среднем
        assert await repo.review_count(1) == 1


async def test_list_reviews_ordered_and_skips_empty(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyMovieRatingRepository(s)
        await repo.set_review(1, 1, "первый", NOW)
        await repo.set_review(1, 2, "второй", NOW.replace(minute=5))
        await s.commit()
        assert [t for _, _, t in await repo.list_reviews(1)] == ["первый", "второй"]


async def test_list_ratings_includes_score_only_and_reviews(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyMovieRatingRepository(s)
        await repo.upsert(1, 1, 9, NOW)                    # только цифра
        await repo.set_review(1, 2, "класс", NOW.replace(minute=1))  # только текст
        await repo.upsert(1, 3, 7, NOW.replace(minute=2))
        await repo.set_review(1, 3, "норм", NOW.replace(minute=2))   # и то и то
        await s.commit()
        ratings = await repo.list_ratings(1)
        by_user = {u: (sc, tx) for u, sc, tx in ratings}
        assert by_user[1] == (9, "")          # цифра без текста
        assert by_user[2] == (None, "класс")  # текст без цифры
        assert by_user[3] == (7, "норм")      # оба
        assert len(ratings) == 3


# --- use-cases ---------------------------------------------------------------

async def _seed_rating_entry(uow_factory, rating_message_id=500, status="rating"):
    async with uow_factory() as uow:
        entry = await uow.movies.add(make_entry(
            rating_message_id=rating_message_id, status=status))
        await uow.commit()
        return entry


async def test_review_use_case_ok(uow_factory):
    entry = await _seed_rating_entry(uow_factory)
    result = await ReviewMovieUseCase(uow_factory).execute(500, 7, "  моя рецензия  ", NOW)
    assert result.status == "ok"
    assert result.reviews == 1

    reviews = await GetMovieReviewsUseCase(uow_factory).execute(entry.id)
    assert len(reviews) == 1
    assert reviews[0].user_id == 7
    assert reviews[0].text == "моя рецензия"  # обрезаны пробелы
    assert reviews[0].score is None


async def test_review_use_case_closed_when_not_rating(uow_factory):
    await _seed_rating_entry(uow_factory, status="watched")
    result = await ReviewMovieUseCase(uow_factory).execute(500, 7, "поздно", NOW)
    assert result.status == "closed"


async def test_review_use_case_unknown_message(uow_factory):
    result = await ReviewMovieUseCase(uow_factory).execute(999, 7, "нет карточки", NOW)
    assert result.status == "closed"


async def test_rate_result_reports_review_count(uow_factory):
    entry = await _seed_rating_entry(uow_factory)
    await ReviewMovieUseCase(uow_factory).execute(500, 1, "отзыв", NOW)
    # оценка тем же сообщением видит и число отзывов
    rate = await RateMovieUseCase(uow_factory).execute(500, 2, 9, NOW)
    assert rate.count == 1 and rate.reviews == 1


# --- окно сбора оценок -------------------------------------------------------

async def test_open_rating_minutes_override(uow_factory):
    # фильм в статусе listed
    async with uow_factory() as uow:
        entry = await uow.movies.add(make_entry(status="listed", rating_message_id=0))
        await uow.commit()
    # минутный режим для тестов: окно закрывается через 1 минуту, а не 24 ч
    result = await OpenRatingUseCase(uow_factory, rating_hours=24, rating_minutes=1).execute(
        entry.id, NOW
    )
    assert result.rating_ends_at == NOW + timedelta(minutes=1)


async def test_open_rating_defaults_to_hours(uow_factory):
    async with uow_factory() as uow:
        entry = await uow.movies.add(make_entry(status="listed", rating_message_id=0))
        await uow.commit()
    result = await OpenRatingUseCase(uow_factory, rating_hours=24).execute(entry.id, NOW)
    assert result.rating_ends_at == NOW + timedelta(hours=24)
