"""Полный список участников сервера через бот-токен.

Требует Server Members Intent у приложения (у бота включён). Discord отдаёт до
1000 участников за запрос — листаем по курсору `after` (id последнего). Ботов
пропускаем: во вкладке «Люди» — люди. Имя берём серверное (nick), иначе
глобальное/логин.
"""

import aiohttp

from src.api.discord_oauth import DISCORD_API
from src.api.discord_users import avatar_url

_PAGE = 1000  # максимум за запрос у Discord


async def fetch_guild_members(bot_token: str, guild_id: int, cap: int = 2000) -> list[dict]:
    headers = {"Authorization": f"Bot {bot_token}"}
    out: list[dict] = []
    after = 0
    async with aiohttp.ClientSession(headers=headers) as session:
        while len(out) < cap:
            params = {"limit": _PAGE, "after": after}
            try:
                async with session.get(
                    f"{DISCORD_API}/guilds/{guild_id}/members", params=params
                ) as resp:
                    if resp.status != 200:
                        break
                    batch = await resp.json()
            except aiohttp.ClientError:
                break
            if not batch:
                break
            for m in batch:
                user = m.get("user") or {}
                if user.get("bot") or "id" not in user:
                    continue
                uid = int(user["id"])
                name = m.get("nick") or user.get("global_name") or user.get("username")
                out.append(
                    {"user_id": uid, "name": name, "avatar": avatar_url(uid, user.get("avatar"))}
                )
            after = int(batch[-1]["user"]["id"])
            if len(batch) < _PAGE:
                break
    return out


async def fetch_guild_member(bot_token: str, guild_id: int, user_id: int) -> dict | None:
    """Один участник сервера или None, если его там нет (404). Дёшево — для
    точечной проверки «сидит ли отмеченный человек на этом сервере»."""
    headers = {"Authorization": f"Bot {bot_token}"}
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(f"{DISCORD_API}/guilds/{guild_id}/members/{user_id}") as resp:
                if resp.status != 200:
                    return None
                m = await resp.json()
    except aiohttp.ClientError:
        return None
    user = m.get("user") or {}
    if "id" not in user:
        return None
    uid = int(user["id"])
    name = m.get("nick") or user.get("global_name") or user.get("username")
    return {"user_id": uid, "name": name, "avatar": avatar_url(uid, user.get("avatar"))}
