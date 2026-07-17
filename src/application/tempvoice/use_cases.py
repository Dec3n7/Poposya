from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.tempvoice.entities import TempChannel

UowFactory = Callable[[], IUnitOfWork]


class RegisterTempChannelUseCase:
    """Запоминает созданную каморку: канал -> владелец."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, channel_id: int, owner_id: int, now: datetime) -> None:
        async with self._uow_factory() as uow:
            await uow.temp_voice.register(
                TempChannel(
                    guild_id=guild_id,
                    channel_id=channel_id,
                    owner_id=owner_id,
                    created_at=now,
                )
            )
            await uow.commit()


class ReleaseTempChannelUseCase:
    """Забывает каморку: канал удалён или подметён после рестарта."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, channel_id: int) -> bool:
        async with self._uow_factory() as uow:
            removed = await uow.temp_voice.release(channel_id)
            await uow.commit()
            return removed


class GetTempChannelUseCase:
    """Каморка по id канала; None — канал не наш (авторизация кнопок)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, channel_id: int) -> TempChannel | None:
        async with self._uow_factory() as uow:
            return await uow.temp_voice.get(channel_id)


class CountTempChannelsUseCase:
    """Сколько каморок живо на сервере — ког сверяет с потолком."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int) -> int:
        async with self._uow_factory() as uow:
            return await uow.temp_voice.count_for_guild(guild_id)


class ListTempChannelsUseCase:
    """Все каморки сервера — подмести осиротевшие после рестарта."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int) -> list[TempChannel]:
        async with self._uow_factory() as uow:
            return await uow.temp_voice.list_for_guild(guild_id)


@dataclass(frozen=True)
class ClaimResult:
    ok: bool
    # not_temp | already_owner | owner_present | not_in_channel
    reason: str = ""
    owner_id: int = 0  # прежний владелец — ког упоминает его в отказе


class ClaimTempChannelUseCase:
    """Передаёт каморку новому владельцу.

    Забрать можно, только если прежнего владельца в канале уже нет, а сам
    забирающий в нём есть — иначе каморку можно было бы увести у хозяина
    или у случайного прохожего снаружи."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self, channel_id: int, new_owner_id: int, present_ids: Collection[int]
    ) -> ClaimResult:
        async with self._uow_factory() as uow:
            channel = await uow.temp_voice.get(channel_id)
            if channel is None:
                return ClaimResult(False, "not_temp")
            # владелец всегда среди присутствующих, поэтому проверяем до owner_present
            if channel.owner_id == new_owner_id:
                return ClaimResult(False, "already_owner", channel.owner_id)
            if channel.owner_id in present_ids:
                return ClaimResult(False, "owner_present", channel.owner_id)
            if new_owner_id not in present_ids:
                return ClaimResult(False, "not_in_channel", channel.owner_id)
            await uow.temp_voice.set_owner(channel_id, new_owner_id)
            await uow.commit()
            return ClaimResult(True, "", channel.owner_id)
