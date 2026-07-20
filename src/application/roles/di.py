from dataclasses import dataclass

from src.application.roles.use_cases import (
    DeleteRoleUseCase,
    ListRolesUseCase,
    SyncGuildRolesUseCase,
    UpsertRoleUseCase,
)


@dataclass(frozen=True)
class RolesContainer:
    sync_guild: SyncGuildRolesUseCase
    upsert_role: UpsertRoleUseCase
    delete_role: DeleteRoleUseCase
    list_roles: ListRolesUseCase
