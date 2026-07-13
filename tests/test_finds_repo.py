"""Прямые тесты SQL-репозиториев модуля finds на SQLite: находки, коллекция,
попытки/кулдаун, атомарный claim."""

from datetime import UTC, datetime, timedelta

from src.domain.finds.entities import CollectionItem, FindAttempt, NightFind
from src.infrastructure.db.repositories.finds import (
    SqlAlchemyCollectionRepository,
    SqlAlchemyFindAttemptRepository,
    SqlAlchemyNightFindRepository,
)

NOW = datetime(2026, 7, 11, 22, 0, tzinfo=UTC)


def make_find(**over):
    base = dict(
        guild_id=10,
        location_id="park",
        item_id="acorn",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        channel_id=100,
        message_id=200,
    )
    base.update(over)
    return NightFind(**base)


# --- NightFind --------------------------------------------------------------


async def test_add_assigns_id_and_get(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyNightFindRepository(s)
        find = await repo.add(make_find())
        await s.commit()
        assert find.id is not None
        loaded = await repo.get(find.id)
        assert loaded.item_id == "acorn"
        assert loaded.expires_at == NOW + timedelta(hours=1)


async def test_get_by_message(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyNightFindRepository(s)
        await repo.add(make_find(message_id=999))
        await s.commit()
        found = await repo.get_by_message(999)
        assert found is not None and found.message_id == 999
        assert await repo.get_by_message(123) is None


async def test_get_active_and_list_unclaimed(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyNightFindRepository(s)
        await repo.add(make_find(item_id="fresh"))
        await repo.add(make_find(item_id="expired", expires_at=NOW - timedelta(minutes=1)))
        await s.commit()

        active = await repo.get_active(10, NOW)
        assert active is not None and active.item_id == "fresh"
        unclaimed = await repo.list_unclaimed(NOW)
        assert [f.item_id for f in unclaimed] == ["fresh"]


async def test_claim_if_free_is_atomic(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyNightFindRepository(s)
        find = await repo.add(make_find())
        await s.commit()
        # первый успевает
        assert await repo.claim_if_free(find.id, user_id=1, now=NOW) is True
        await s.commit()
        # второй — уже занято
        assert await repo.claim_if_free(find.id, user_id=2, now=NOW) is False
        await s.commit()
        loaded = await repo.get(find.id)
        assert loaded.claimed_by == 1
        # больше не активна и не в списке
        assert await repo.get_active(10, NOW) is None


async def test_save_updates_fields(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyNightFindRepository(s)
        find = await repo.add(make_find())
        await s.commit()
        find.message_id = 777
        find.claimed_by = 5
        find.claimed_at = NOW
        await repo.save(find)
        await s.commit()
        loaded = await repo.get(find.id)
        assert loaded.message_id == 777 and loaded.claimed_by == 5


async def test_save_new_without_id_inserts(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyNightFindRepository(s)
        find = make_find()  # id None
        await repo.save(find)  # должен вставить через add
        await s.commit()
        assert find.id is not None


# --- Collection -------------------------------------------------------------


async def test_collection_add_list_and_gift(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyCollectionRepository(s)
        await repo.add(CollectionItem(guild_id=10, user_id=1, item_id="acorn", obtained_at=NOW))
        await repo.add(
            CollectionItem(
                guild_id=10,
                user_id=1,
                item_id="leaf",
                obtained_at=NOW + timedelta(minutes=1),
            )
        )
        await s.commit()

        items = await repo.list_for_user(10, 1)
        assert [i.item_id for i in items] == ["acorn", "leaf"]

        ungifted = await repo.get_ungifted(10, 1, "acorn")
        assert ungifted is not None
        await repo.mark_gifted(ungifted.id, NOW)
        await s.commit()
        # подаренный больше не выдаётся как ungifted
        assert await repo.get_ungifted(10, 1, "acorn") is None


async def test_collection_get_ungifted_missing(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyCollectionRepository(s)
        assert await repo.get_ungifted(10, 1, "nope") is None


# --- FindAttempt ------------------------------------------------------------


async def test_attempt_last_at_and_cooldown(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyFindAttemptRepository(s)
        assert await repo.last_attempt_at(10, 1, "walk") is None
        await repo.add(
            FindAttempt(
                guild_id=10,
                user_id=1,
                kind="walk",
                success=True,
                attempted_at=NOW,
            )
        )
        later = NOW + timedelta(hours=1)
        await repo.add(
            FindAttempt(
                guild_id=10,
                user_id=1,
                kind="walk",
                success=False,
                attempted_at=later,
            )
        )
        await s.commit()
        # берётся самая свежая попытка
        assert await repo.last_attempt_at(10, 1, "walk") == later
        # другой kind не смешивается
        assert await repo.last_attempt_at(10, 1, "claim") is None


async def test_has_attempted(session_factory):
    async with session_factory() as s:
        repo = SqlAlchemyFindAttemptRepository(s)
        await repo.add(
            FindAttempt(
                guild_id=10,
                user_id=1,
                kind="claim",
                success=False,
                attempted_at=NOW,
                find_id=55,
            )
        )
        await s.commit()
        assert await repo.has_attempted(55, 1) is True
        assert await repo.has_attempted(55, 2) is False
        assert await repo.has_attempted(99, 1) is False
