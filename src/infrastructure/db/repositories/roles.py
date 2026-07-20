from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.roles.entities import GuildRole, RoleMeta
from src.domain.roles.repository import IRoleRepository
from src.infrastructure.db.models.roles import GuildRoleMetaModel, GuildRoleModel


def _to_domain(row: GuildRoleModel) -> GuildRole:
    return GuildRole(
        guild_id=row.guild_id,
        role_id=row.role_id,
        name=row.name,
        color=row.color,
        hoist=row.hoist,
        mentionable=row.mentionable,
        position=row.position,
        managed=row.managed,
        permissions=row.permissions,
    )


def _model(role: GuildRole, now: datetime) -> GuildRoleModel:
    return GuildRoleModel(
        role_id=role.role_id,
        guild_id=role.guild_id,
        name=role.name,
        color=role.color,
        hoist=role.hoist,
        mentionable=role.mentionable,
        position=role.position,
        managed=role.managed,
        permissions=role.permissions,
        updated_at=now,
    )


class SqlAlchemyRoleRepository(IRoleRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def replace_guild_roles(
        self, guild_id: int, roles: list[GuildRole], now: datetime
    ) -> None:
        # снести и переложить — самый простой способ сойтись с Discord один в один
        # (учитывает и удалённые, пока бот лежал, роли)
        await self._session.execute(
            delete(GuildRoleModel).where(GuildRoleModel.guild_id == guild_id)
        )
        self._session.add_all(_model(role, now) for role in roles)

    async def upsert_role(self, role: GuildRole, now: datetime) -> None:
        await self._session.merge(_model(role, now))

    async def delete_role(self, guild_id: int, role_id: int) -> None:
        await self._session.execute(
            delete(GuildRoleModel).where(GuildRoleModel.role_id == role_id)
        )

    async def set_meta(self, meta: RoleMeta) -> None:
        await self._session.merge(
            GuildRoleMetaModel(
                guild_id=meta.guild_id,
                bot_user_id=meta.bot_user_id,
                bot_top_position=meta.bot_top_position,
                synced_at=meta.synced_at,
            )
        )

    async def list_roles(self, guild_id: int) -> list[GuildRole]:
        stmt = select(GuildRoleModel).where(GuildRoleModel.guild_id == guild_id)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(row) for row in rows]

    async def get_meta(self, guild_id: int) -> RoleMeta | None:
        row = await self._session.get(GuildRoleMetaModel, guild_id)
        if row is None:
            return None
        return RoleMeta(
            guild_id=row.guild_id,
            bot_user_id=row.bot_user_id,
            bot_top_position=row.bot_top_position,
            synced_at=row.synced_at,
        )
