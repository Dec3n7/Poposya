"""Разрешение Discord-пользователей (имя + аватар) по id — для лидерборда.

Bulk-эндпоинта у Discord нет, поэтому тянем по одному параллельно. Сбой на
любом юзере не роняет ответ — просто имя будет None (фронт покажет id).
"""

import asyncio
import time

import aiohttp

from src.api.discord_oauth import DISCORD_API

CDN = "https://cdn.discordapp.com"

# Кэш имён/аватаров: у Discord нет bulk-эндпоинта, а live-поллинг плеера (~3с)
# иначе бил бы по /users на каждый опрос. Имена/аватары меняются редко.
_USER_CACHE: dict[int, tuple[float, dict]] = {}
_USER_TTL = 300.0


def avatar_url(user_id: int | str, avatar_hash: str | None) -> str | None:
    """CDN-URL аватара пользователя по id + хэшу (None -> фронт покажет инициалы)."""
    if not avatar_hash:
        return None
    return f"{CDN}/avatars/{user_id}/{avatar_hash}.png?size=64"


def guild_icon_url(guild_id: int | str, icon_hash: str | None) -> str | None:
    """CDN-URL иконки сервера по id + хэшу."""
    if not icon_hash:
        return None
    return f"{CDN}/icons/{guild_id}/{icon_hash}.png?size=64"


def _avatar_url(user: dict) -> str | None:
    return avatar_url(user.get("id", ""), user.get("avatar"))


async def fetch_users(bot_token: str, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    now = time.monotonic()
    out: dict[int, dict] = {}
    missing: list[int] = []
    for uid in set(ids):
        hit = _USER_CACHE.get(uid)
        if hit is not None and now - hit[0] < _USER_TTL:
            out[uid] = hit[1]
        else:
            missing.append(uid)
    if not missing:
        return out

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

        results = await asyncio.gather(*[one(uid) for uid in missing])

    for uid, user in results:
        if user is None:
            resolved = {"username": None, "avatar": None}
        else:
            resolved = {
                "username": user.get("global_name") or user.get("username"),
                "avatar": _avatar_url(user),
            }
        # None-имя не кэшируем надолго (мог быть временный сбой) — только успешные
        if resolved["username"] is not None:
            _USER_CACHE[uid] = (now, resolved)
        out[uid] = resolved
    return out
