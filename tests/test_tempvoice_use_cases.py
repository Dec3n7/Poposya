"""Use case'ы каморок. Основное внимание — «Забрать»: единственное правило
модуля, всё остальное здесь — тонкая обёртка над репозиторием."""

from datetime import UTC, datetime

import pytest

from src.application.tempvoice.use_cases import (
    ClaimTempChannelUseCase,
    CountTempChannelsUseCase,
    GetTempChannelUseCase,
    ListTempChannelsUseCase,
    RegisterTempChannelUseCase,
    ReleaseTempChannelUseCase,
)

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)

OWNER = 1
STRANGER = 2


@pytest.fixture
async def registered(uow_factory):
    """Каморка 100 на сервере 10, владелец OWNER."""
    await RegisterTempChannelUseCase(uow_factory).execute(10, 100, OWNER, NOW)


async def test_register_then_get(uow_factory):
    await RegisterTempChannelUseCase(uow_factory).execute(10, 100, OWNER, NOW)
    found = await GetTempChannelUseCase(uow_factory).execute(100)
    assert found is not None
    assert (found.guild_id, found.owner_id, found.created_at.replace(tzinfo=UTC)) == (
        10,
        OWNER,
        NOW,
    )


async def test_get_returns_none_for_foreign_channel(uow_factory):
    assert await GetTempChannelUseCase(uow_factory).execute(999) is None


async def test_release(uow_factory, registered):
    assert await ReleaseTempChannelUseCase(uow_factory).execute(100) is True
    assert await GetTempChannelUseCase(uow_factory).execute(100) is None
    assert await ReleaseTempChannelUseCase(uow_factory).execute(100) is False


async def test_count_and_list(uow_factory):
    register = RegisterTempChannelUseCase(uow_factory)
    await register.execute(10, 100, OWNER, NOW)
    await register.execute(10, 101, STRANGER, NOW)
    await register.execute(20, 200, OWNER, NOW)  # другой сервер
    assert await CountTempChannelsUseCase(uow_factory).execute(10) == 2
    listed = await ListTempChannelsUseCase(uow_factory).execute(10)
    assert {c.channel_id for c in listed} == {100, 101}


# --- «Забрать» ---


async def test_claim_succeeds_when_owner_left(uow_factory, registered):
    # владельца в канале нет, забирающий — есть
    result = await ClaimTempChannelUseCase(uow_factory).execute(100, STRANGER, {STRANGER})
    assert result.ok
    assert result.owner_id == OWNER  # прежний владелец — для сообщения
    found = await GetTempChannelUseCase(uow_factory).execute(100)
    assert found.owner_id == STRANGER


async def test_claim_rejected_while_owner_present(uow_factory, registered):
    result = await ClaimTempChannelUseCase(uow_factory).execute(100, STRANGER, {OWNER, STRANGER})
    assert (result.ok, result.reason, result.owner_id) == (False, "owner_present", OWNER)
    found = await GetTempChannelUseCase(uow_factory).execute(100)
    assert found.owner_id == OWNER  # владелец не сменился


async def test_claim_rejected_for_outsider(uow_factory, registered):
    # забирающего нет в канале — уводить каморку снаружи нельзя
    result = await ClaimTempChannelUseCase(uow_factory).execute(100, STRANGER, {3})
    assert (result.ok, result.reason) == (False, "not_in_channel")
    found = await GetTempChannelUseCase(uow_factory).execute(100)
    assert found.owner_id == OWNER


async def test_claim_by_owner_himself(uow_factory, registered):
    result = await ClaimTempChannelUseCase(uow_factory).execute(100, OWNER, {OWNER})
    assert (result.ok, result.reason) == (False, "already_owner")


async def test_claim_on_foreign_channel(uow_factory):
    result = await ClaimTempChannelUseCase(uow_factory).execute(999, STRANGER, {STRANGER})
    assert (result.ok, result.reason) == (False, "not_temp")


async def test_claim_twice_hands_channel_over_each_time(uow_factory, registered):
    claim = ClaimTempChannelUseCase(uow_factory)
    assert (await claim.execute(100, STRANGER, {STRANGER})).ok
    # теперь владелец STRANGER; он вышел, забирает третий
    result = await claim.execute(100, 3, {3})
    assert result.ok
    assert result.owner_id == STRANGER
    found = await GetTempChannelUseCase(uow_factory).execute(100)
    assert found.owner_id == 3
