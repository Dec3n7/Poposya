from collections.abc import Callable
from datetime import UTC, datetime

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.player.entities import PlayerState

UowFactory = Callable[[], IUnitOfWork]


class SavePlayerStateUseCase:
    """Сохраняет снапшот плеера (upsert). Ставит position_at/updated_at на «сейчас»
    — момент замера позиции ботом; панель по ним тикает прогресс."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, state: PlayerState) -> None:
        now = datetime.now(UTC)
        state.updated_at = now
        if state.position_at is None:
            state.position_at = now
        async with self._uow_factory() as uow:
            await uow.player_state.save(state)
            await uow.commit()


class GetPlayerStateUseCase:
    """Текущий снапшот плеера гильдии (или None, если бот ничего не писал)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int) -> PlayerState | None:
        async with self._uow_factory() as uow:
            return await uow.player_state.get(guild_id)
