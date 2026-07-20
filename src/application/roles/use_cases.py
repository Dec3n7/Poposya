from collections.abc import Callable
from datetime import UTC, datetime

from src.application.interfaces.unit_of_work import IUnitOfWork
from src.domain.roles.entities import GuildRole, RoleMeta

UowFactory = Callable[[], IUnitOfWork]


def _now() -> datetime:
    # модели хранят naive UTC (как остальные таблицы проекта)
    return datetime.now(UTC).replace(tzinfo=None)


class SyncGuildRolesUseCase:
    """Полный бэкфилл зеркала ролей сервера + мета (граница бота). Бот зовёт на
    старте и при входе на новый сервер — снести и переложить, чтобы сойтись с
    Discord один в один."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self, guild_id: int, roles: list[GuildRole], bot_user_id: int, bot_top_position: int
    ) -> None:
        now = _now()
        async with self._uow_factory() as uow:
            await uow.roles.replace_guild_roles(guild_id, roles, now)
            await uow.roles.set_meta(RoleMeta(guild_id, bot_user_id, bot_top_position, now))
            await uow.commit()


class UpsertRoleUseCase:
    """Одна роль создана/изменена (gateway-событие) — освежить её и мету: позиция
    высшей роли бота могла сдвинуться, если двигали роли."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, role: GuildRole, bot_user_id: int, bot_top_position: int) -> None:
        now = _now()
        async with self._uow_factory() as uow:
            await uow.roles.upsert_role(role, now)
            await uow.roles.set_meta(RoleMeta(role.guild_id, bot_user_id, bot_top_position, now))
            await uow.commit()


class DeleteRoleUseCase:
    """Роль удалена (gateway-событие) — убрать из зеркала."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self, guild_id: int, role_id: int, bot_user_id: int, bot_top_position: int
    ) -> None:
        now = _now()
        async with self._uow_factory() as uow:
            await uow.roles.delete_role(guild_id, role_id)
            await uow.roles.set_meta(RoleMeta(guild_id, bot_user_id, bot_top_position, now))
            await uow.commit()


class ListRolesUseCase:
    """Роли сервера из зеркала + мета (для панели). Чистое чтение."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int) -> tuple[list[GuildRole], RoleMeta | None]:
        async with self._uow_factory() as uow:
            roles = await uow.roles.list_roles(guild_id)
            meta = await uow.roles.get_meta(guild_id)
            return roles, meta
