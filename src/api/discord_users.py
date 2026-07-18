"""Разрешение Discord-пользователей (имя + аватар) по id — для лидерборда.

Bulk-эндпоинта у Discord нет, поэтому тянем по одному параллельно. Сбой на
любом юзере не роняет ответ — просто имя будет None (фронт покажет id).
"""

import asyncio

import aiohttp

from src.api.discord_oauth import DISCORD_API


def _avatar_url(user: dict) -> str | None:
    avatar = user.get("avatar")
    if not avatar:
        return None
    return f"https://cdn.discordapp.com/avatars/{user['id']}/{avatar}.png?size=64"


async def fetch_users(bot_token: str, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    headers = {"Authorization": f"Bot {bot_token}"}
    async with aiohttp.ClientSession(headers=headers) as session:

        async def one(uid: int) -> tuple[int, dict | None]:
            try:
                async with session.get(f"{DISCORD_API}/users/{uid}") as resp:
                    if resp.status != 200:
                        return uid, None
                    return uid, await resp.json()
            except aiohttp.ClientError:
                return uid, None

        results = await asyncio.gather(*[one(uid) for uid in ids])

    out: dict[int, dict] = {}
    for uid, user in results:
        if user is None:
            out[uid] = {"username": None, "avatar": None}
        else:
            out[uid] = {
                "username": user.get("global_name") or user.get("username"),
                "avatar": _avatar_url(user),
            }
    return out
