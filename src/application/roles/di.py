from dataclasses import dataclass

from src.application.roles.use_cases import (
    DeleteRoleUseCase,
    ListRolesUseCase,
    MemberRolesUseCase,
    RemoveMemberUseCase,
    SetMemberRolesUseCase,
    SyncGuildRolesUseCase,
    SyncMembersUseCase,
    UpsertRoleUseCase,
)


@dataclass(frozen=True)
class RolesContainer:
    sync_guild: SyncGuildRolesUseCase
    upsert_role: UpsertRoleUseCase
    delete_role: DeleteRoleUseCase
    list_roles: ListRolesUseCase
    sync_members: SyncMembersUseCase
    set_member_roles: SetMemberRolesUseCase
    remove_member: RemoveMemberUseCase
    member_roles: MemberRolesUseCase
