"""Репозиторий temp_voice_channels: регистрация, смена владельца, освобождение,
счётчик каналов сервера (поверх реального SQLite)."""

from datetime import UTC, datetime

from src.domain.tempvoice.entities import TempChannel

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def _ch(channel_id=100, guild_id=10, owner_id=1):
    return TempChannel(
        guild_id=guild_id,
        channel_id=channel_id,
        owner_id=owner_id,
        created_at=NOW,
    )


async def test_register_and_get(uow_factory):
    async with uow_factory() as uow:
        await uow.temp_voice.register(_ch())
        await uow.commit()
    async with uow_factory() as uow:
        found = await uow.temp_voice.get(100)
    assert found is not None
    assert (found.guild_id, found.owner_id) == (10, 1)


async def test_get_unknown_channel(uow_factory):
    async with uow_factory() as uow:
        assert await uow.temp_voice.get(999) is None


async def test_release(uow_factory):
    async with uow_factory() as uow:
        await uow.temp_voice.register(_ch())
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.temp_voice.release(100) is True
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.temp_voice.release(100) is False  # уже нет
        assert await uow.temp_voice.get(100) is None


async def test_set_owner(uow_factory):
    async with uow_factory() as uow:
        await uow.temp_voice.register(_ch(owner_id=1))
        await uow.commit()
    async with uow_factory() as uow:
        await uow.temp_voice.set_owner(100, 2)  # канал забрали
        await uow.commit()
    async with uow_factory() as uow:
        found = await uow.temp_voice.get(100)
    assert found.owner_id == 2


async def test_list_and_count_are_per_guild(uow_factory):
    async with uow_factory() as uow:
        await uow.temp_voice.register(_ch(channel_id=100, guild_id=10))
        await uow.temp_voice.register(_ch(channel_id=101, guild_id=10))
        await uow.temp_voice.register(_ch(channel_id=200, guild_id=20))
        await uow.commit()
    async with uow_factory() as uow:
        ours = await uow.temp_voice.list_for_guild(10)
        assert await uow.temp_voice.count_for_guild(10) == 2
        assert await uow.temp_voice.count_for_guild(20) == 1
        assert await uow.temp_voice.count_for_guild(30) == 0  # каналов не было
    assert {c.channel_id for c in ours} == {100, 101}


async def test_count_drops_after_release(uow_factory):
    async with uow_factory() as uow:
        await uow.temp_voice.register(_ch(channel_id=100))
        await uow.temp_voice.register(_ch(channel_id=101))
        await uow.commit()
    async with uow_factory() as uow:
        await uow.temp_voice.release(100)
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.temp_voice.count_for_guild(10) == 1
