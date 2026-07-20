"""Роли сервера (read-only на этом этапе): панель читает зеркало ролей, которое
бот держит актуальным. Мутации (CRUD/порядок/выдача) добавятся отдельной фазой
через командный мост — здесь их пока НЕТ.

`editable` считает бэкенд: роль доступна боту, только если она НИЖЕ его высшей
роли и не managed/@everyone. Фронт по этому флагу рисует границу и блокировки.
"""

from fastapi import APIRouter, Depends

from src.api.container import ApiContainer
from src.api.dependencies import get_container, require_guild_manager

router = APIRouter(prefix="/api/guilds/{guild_id}/roles", tags=["roles"])


@router.get("")
async def list_roles(
    guild_id: int = Depends(require_guild_manager),
    container: ApiContainer = Depends(get_container),
) -> dict:
    roles, meta = await container.list_roles.execute(guild_id)
    bot_top = meta.bot_top_position if meta is not None else None

    def editable(position: int, managed: bool, is_default: bool) -> bool:
        if bot_top is None or managed or is_default:
            return False
        return position < bot_top

    ordered = sorted(roles, key=lambda r: r.position, reverse=True)
    return {
        "bot_top_position": bot_top,
        "bot_user_id": str(meta.bot_user_id) if meta is not None else None,
        "synced_at": meta.synced_at.isoformat() if meta is not None else None,
        "roles": [
            {
                "id": str(r.role_id),
                "name": r.name,
                "color": r.color,
                "hoist": r.hoist,
                "mentionable": r.mentionable,
                "position": r.position,
                "managed": r.managed,
                "permissions": str(r.permissions),
                # @everyone: его id совпадает с id сервера
                "is_default": r.role_id == guild_id,
                "editable": editable(r.position, r.managed, r.role_id == guild_id),
            }
            for r in ordered
        ],
    }
