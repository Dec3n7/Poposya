"""Кэш серверов, где стоит бот.

Пользователь может «управлять» кучей серверов, но настраивать в панели есть смысл
только те, где бот реально присутствует. Список серверов бота узнаём запросом к
Discord с bot-токеном и кэшируем на несколько минут (меняется редко, а Discord
лимитирует частоту).
"""

import asyncio
import time

import aiohttp

from src.api.discord_oauth import DISCORD_API, OAuthError


async def _fetch_bot_guild_ids(bot_token: str) -> set[int]:
    headers = {"Authorization": f"Bot {bot_token}"}
    ids: set[int] = set()
    after: str | None = None
    async with aiohttp.ClientSession(headers=headers) as session:
        while True:  # пагинация: Discord отдаёт максимум 200 за раз
            params: dict[str, int | str] = {"limit": 200}
            if after is not None:
                params["after"] = after
            async with session.get(f"{DISCORD_API}/users/@me/guilds", params=params) as resp:
                if resp.status != 200:
                    raise OAuthError(f"bot guilds fetch failed: HTTP {resp.status}")
                batch = await resp.json()
            if not batch:
                break
            for guild in batch:
                ids.add(int(guild["id"]))
            if len(batch) < 200:
                break
            after = batch[-1]["id"]
    return ids


class BotGuildsCache:
    def __init__(self, bot_token: str, ttl_seconds: int = 300):
        self._token = bot_token
        self._ttl = ttl_seconds
        self._ids: set[int] = set()
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> set[int]:
        if self._fresh():
            return self._ids
        async with self._lock:
            if self._fresh():  # мог обновиться, пока ждали лок
                return self._ids
            self._ids = await _fetch_bot_guild_ids(self._token)
            self._fetched_at = time.monotonic()
            return self._ids

    def _fresh(self) -> bool:
        return self._fetched_at > 0.0 and (time.monotonic() - self._fetched_at) < self._ttl

    def prime(self, ids: set[int]) -> None:
        """Подставить список напрямую (для тестов — без похода в Discord)."""
        self._ids = set(ids)
        self._fetched_at = time.monotonic()
