from collections.abc import Callable
from datetime import UTC, datetime

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.botprofile.entities import BotProfile

UowFactory = Callable[[], IUnitOfWork]


class GetBotProfileUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int) -> BotProfile:
        async with self._uow_factory() as uow:
            profile = await uow.bot_profile.get(guild_id)
        return profile or BotProfile(guild_id=guild_id)


class SetBotProfileUseCase:
    """Сохраняет пер-серверный профиль бота (upsert). Применяет его к Discord
    не тут, а через командный мост (guild.me.edit)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self,
        guild_id: int,
        nick: str,
        avatar_url: str,
        banner_url: str,
        avatar_data: str = "",
        banner_data: str = "",
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.bot_profile.save(
                BotProfile(
                    guild_id=guild_id,
                    nick=nick.strip(),
                    avatar_url=avatar_url.strip(),
                    banner_url=banner_url.strip(),
                    avatar_data=avatar_data.strip(),
                    banner_data=banner_data.strip(),
                    updated_at=datetime.now(UTC),
                )
            )
            await uow.commit()
