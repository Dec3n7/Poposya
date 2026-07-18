"""Каналы сервера из Discord (bot-токен) — для пикера канала в настройках.

Настройки-каналы (форум киноклуба, канал находок, хаб/категория каморок) в
панели выбираются из списка, а не вводятся ID руками.
"""

import aiohttp

from src.api.discord_oauth import DISCORD_API, OAuthError

# типы каналов Discord -> человекочитаемая группа (для optgroup в пикере)
_CHANNEL_GROUPS = {
    0: "Текстовые",
    2: "Голосовые",
    4: "Категории",
    5: "Анонсы",
    13: "Трибуны",
    15: "Форумы",
}


async def fetch_guild_channels(bot_token: str, guild_id: int) -> list[dict]:
    headers = {"Authorization": f"Bot {bot_token}"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(f"{DISCORD_API}/guilds/{guild_id}/channels") as resp:
            if resp.status != 200:
                raise OAuthError(f"channels fetch failed: HTTP {resp.status}")
            data = await resp.json()
    channels: list[dict] = []
    for c in data:
        group = _CHANNEL_GROUPS.get(c.get("type"))
        if group is None:
            continue
        channels.append(
            {
                "id": str(c["id"]),  # snowflake -> строкой
                "name": c.get("name", ""),
                "group": group,
                "position": c.get("position", 0),
            }
        )
    channels.sort(key=lambda c: (c["group"], c["position"]))
    return channels
