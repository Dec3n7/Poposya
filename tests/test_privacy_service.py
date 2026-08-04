"""PrivacyService: удаление данных сервера (purge_guild), участника (forget_user)
и окно отсрочки (mark/cancel/sweep) поверх реального SQLite."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from src.infrastructure.db.models.activity import MemberActivityModel, ReminderModel
from src.infrastructure.db.models.appeals import AppealModel
from src.infrastructure.db.models.banwatch import ServerBanModel
from src.infrastructure.db.models.cinema import (
    MovieEntryModel,
    MovieNightModel,
    MovieNightVoteModel,
    MovieRatingModel,
    MovieVoteModel,
)
from src.infrastructure.db.models.finds import CollectionItemModel
from src.infrastructure.db.models.guild import GuildSettingModel
from src.infrastructure.db.models.moderation import ModCaseModel, WarnModel
from src.infrastructure.db.models.privacy import GuildDepartureModel
from src.infrastructure.db.models.relationship import (
    DialogSummaryModel,
    RelationshipProfileModel,
)
from src.infrastructure.privacy_service import PrivacyService

NAIVE_NOW = datetime(2026, 8, 1, 12, 0, 0)


@pytest.fixture
def privacy(session_factory):
    return PrivacyService(session_factory, grace_days=30)


async def _count(session_factory, model, **filters) -> int:
    stmt = select(func.count()).select_from(model)
    for col, val in filters.items():
        stmt = stmt.where(getattr(model, col) == val)
    async with session_factory() as session:
        return (await session.execute(stmt)).scalar_one()


async def _seed_guild(session_factory, guild_id: int, user_id: int) -> None:
    """Строка в каждой из ключевых таблиц (личное + модерация + кино + настройки)."""
    async with session_factory() as session:
        session.add(RelationshipProfileModel(user_id=user_id, guild_id=guild_id, points=5))
        session.add(
            DialogSummaryModel(
                guild_id=guild_id, user_id=user_id, summary="s", created_at=NAIVE_NOW
            )
        )
        session.add(
            MemberActivityModel(user_id=user_id, guild_id=guild_id, last_message_at=NAIVE_NOW)
        )
        session.add(ReminderModel(user_id=user_id, guild_id=guild_id, text="r", due_at=NAIVE_NOW))
        session.add(
            CollectionItemModel(
                guild_id=guild_id, user_id=user_id, item_id="itm", obtained_at=NAIVE_NOW
            )
        )
        session.add(GuildSettingModel(guild_id=guild_id, key="warn_threshold", value="5"))
        # модерация
        session.add(
            WarnModel(guild_id=guild_id, user_id=user_id, moderator_id=99, created_at=NAIVE_NOW)
        )
        session.add(
            ModCaseModel(
                guild_id=guild_id,
                user_id=user_id,
                moderator_id=99,
                action="warn",
                created_at=NAIVE_NOW,
            )
        )
        session.add(ServerBanModel(user_id=user_id, guild_id=guild_id))
        session.add(
            AppealModel(guild_id=guild_id, user_id=user_id, action="ban", created_at=NAIVE_NOW)
        )
        # кино: родитель + «дети» без своего guild_id
        entry = MovieEntryModel(guild_id=guild_id, title="M", added_by=user_id, added_at=NAIVE_NOW)
        night = MovieNightModel(
            guild_id=guild_id, created_by=user_id, scheduled_at=NAIVE_NOW, poll_ends_at=NAIVE_NOW
        )
        session.add_all([entry, night])
        await session.flush()  # нужны id родителей
        session.add(MovieVoteModel(entry_id=entry.id, user_id=user_id, value=1))
        session.add(MovieRatingModel(entry_id=entry.id, user_id=user_id, rated_at=NAIVE_NOW))
        session.add(MovieNightVoteModel(night_id=night.id, user_id=user_id, entry_id=entry.id))
        await session.commit()


# --- purge_guild ------------------------------------------------------------


async def test_purge_guild_wipes_everything_and_isolates_other(privacy, session_factory):
    await _seed_guild(session_factory, 10, 1)
    await _seed_guild(session_factory, 20, 1)

    counts = await privacy.purge_guild(10)

    # сервер 10 очищен полностью, включая модерацию и кино-детей
    for model in (
        RelationshipProfileModel,
        DialogSummaryModel,
        MemberActivityModel,
        ReminderModel,
        CollectionItemModel,
        GuildSettingModel,
        WarnModel,
        ModCaseModel,
        ServerBanModel,
        AppealModel,
        MovieEntryModel,
        MovieNightModel,
    ):
        assert await _count(session_factory, model, guild_id=10) == 0
    assert await _count(session_factory, MovieVoteModel) == 1  # остался только голос сервера 20
    # сервер 20 не тронут
    assert await _count(session_factory, RelationshipProfileModel, guild_id=20) == 1
    assert await _count(session_factory, WarnModel, guild_id=20) == 1
    # счётчики отражают удаление
    assert counts["warns"] == 1
    assert counts["cinema_votes"] == 1
    assert counts["relationship_profiles"] == 1


async def test_purge_guild_removes_departure_mark(privacy, session_factory):
    await privacy.mark_departure(10, datetime.now(UTC))
    assert await _count(session_factory, GuildDepartureModel, guild_id=10) == 1
    await privacy.purge_guild(10)
    assert await _count(session_factory, GuildDepartureModel, guild_id=10) == 0


# --- forget_user ------------------------------------------------------------


async def test_forget_user_deletes_personal_keeps_moderation(privacy, session_factory):
    await _seed_guild(session_factory, 10, 1)
    # второй участник на том же сервере — не должен пострадать
    async with session_factory() as session:
        session.add(RelationshipProfileModel(user_id=2, guild_id=10, points=1))
        await session.commit()

    counts = await privacy.forget_user(10, 1)

    # личное — стёрто
    for model in (
        RelationshipProfileModel,
        DialogSummaryModel,
        MemberActivityModel,
        ReminderModel,
        CollectionItemModel,
    ):
        assert await _count(session_factory, model, guild_id=10, user_id=1) == 0
    assert await _count(session_factory, MovieVoteModel, user_id=1) == 0
    assert await _count(session_factory, MovieRatingModel, user_id=1) == 0
    assert await _count(session_factory, MovieNightVoteModel, user_id=1) == 0
    # модерация — сохранена
    assert await _count(session_factory, WarnModel, guild_id=10, user_id=1) == 1
    assert await _count(session_factory, ModCaseModel, guild_id=10, user_id=1) == 1
    assert await _count(session_factory, ServerBanModel, guild_id=10, user_id=1) == 1
    assert await _count(session_factory, AppealModel, guild_id=10, user_id=1) == 1
    # серверные объекты (запись в каталоге кино, настройки) — не личное, остаются
    assert await _count(session_factory, MovieEntryModel, guild_id=10) == 1
    assert await _count(session_factory, GuildSettingModel, guild_id=10) == 1
    # другой участник цел
    assert await _count(session_factory, RelationshipProfileModel, guild_id=10, user_id=2) == 1
    # в сводке нет модерационных таблиц
    assert "warns" not in counts and "server_bans" not in counts
    assert counts["relationship_profiles"] == 1


async def test_forget_user_is_per_guild(privacy, session_factory):
    async with session_factory() as session:
        session.add(RelationshipProfileModel(user_id=1, guild_id=10, points=5))
        session.add(RelationshipProfileModel(user_id=1, guild_id=20, points=5))
        await session.commit()

    await privacy.forget_user(10, 1)

    assert await _count(session_factory, RelationshipProfileModel, guild_id=10, user_id=1) == 0
    assert await _count(session_factory, RelationshipProfileModel, guild_id=20, user_id=1) == 1


async def test_forget_user_empty_returns_empty(privacy, session_factory):
    assert await privacy.forget_user(10, 777) == {}


# --- окно отсрочки: mark / cancel / sweep ----------------------------------


async def test_mark_and_cancel_departure(privacy, session_factory):
    await privacy.mark_departure(10, datetime.now(UTC))
    assert await _count(session_factory, GuildDepartureModel, guild_id=10) == 1
    # повторная отметка не плодит строк (upsert)
    await privacy.mark_departure(10, datetime.now(UTC))
    assert await _count(session_factory, GuildDepartureModel, guild_id=10) == 1

    assert await privacy.cancel_departure(10) is True
    assert await _count(session_factory, GuildDepartureModel, guild_id=10) == 0
    # отменять нечего
    assert await privacy.cancel_departure(10) is False


async def test_sweep_purges_only_expired(privacy, session_factory):
    now = datetime.now(UTC)
    await _seed_guild(session_factory, 10, 1)
    await _seed_guild(session_factory, 20, 1)
    await privacy.mark_departure(10, now - timedelta(days=40))  # просрочен (>30д)
    await privacy.mark_departure(20, now - timedelta(days=5))  # ещё в окне

    purged = await privacy.sweep_expired(now)

    assert [gid for gid, _ in purged] == [10]
    # 10 стёрт целиком, отметка снята
    assert await _count(session_factory, RelationshipProfileModel, guild_id=10) == 0
    assert await _count(session_factory, GuildDepartureModel, guild_id=10) == 0
    # 20 цел, отметка на месте
    assert await _count(session_factory, RelationshipProfileModel, guild_id=20) == 1
    assert await _count(session_factory, GuildDepartureModel, guild_id=20) == 1
