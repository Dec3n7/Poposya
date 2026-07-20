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
    """Роли сервера из зеркала + мета + счётчики носителей (для панели). Чтение."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self, guild_id: int
    ) -> tuple[list[GuildRole], RoleMeta | None, dict[int, int]]:
        async with self._uow_factory() as uow:
            roles = await uow.roles.list_roles(guild_id)
            meta = await uow.roles.get_meta(guild_id)
            counts = await uow.roles.holder_counts(guild_id)
            return roles, meta, counts


class SyncMembersUseCase:
    """Полный бэкфилл носителей ролей сервера (user_id -> его role_id)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, holders: dict[int, list[int]]) -> None:
        async with self._uow_factory() as uow:
            await uow.roles.replace_member_roles(guild_id, holders)
            await uow.commit()


class SetMemberRolesUseCase:
    """Роли одного участника изменились (gateway) — переложить его строки."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, user_id: int, role_ids: list[int]) -> None:
        async with self._uow_factory() as uow:
            await uow.roles.set_member_roles(guild_id, user_id, role_ids)
            await uow.commit()


class RemoveMemberUseCase:
    """Участник вышел — убрать его из зеркала носителей."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, user_id: int) -> None:
        async with self._uow_factory() as uow:
            await uow.roles.delete_member(guild_id, user_id)
            await uow.commit()


class MemberRolesUseCase:
    """id ролей участника из зеркала (для карточки человека в панели)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, guild_id: int, user_id: int) -> list[int]:
        async with self._uow_factory() as uow:
            return await uow.roles.member_role_ids(guild_id, user_id)
