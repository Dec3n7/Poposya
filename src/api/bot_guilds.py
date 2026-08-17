"""Кэш серверов, где стоит бот.

Пользователь может «управлять» кучей серверов, но настраивать в панели есть смысл
только те, где бот реально присутствует. Список серверов бота узнаём запросом к
Discord с bot-токеном и кэшируем на несколько минут (меняется редко, а Discord
лимитирует частоту).

Помимо самих id кэшируем имя и иконку каждого сервера (Discord отдаёт их в том же
ответе). Это нужно оператору: панель показывает ему ВСЕ серверы бота — даже те,
которыми он не управляет, — а имя/иконку для них взять больше неоткуда (у такого
оператора нет этих серверов в его OAuth-списке).
"""

import asyncio
import time
from dataclasses import dataclass

import aiohttp

from src.api.discord_oauth import DISCORD_API, OAuthError


@dataclass(frozen=True)
class BotGuildMeta:
    name: str
    icon: str | None


async def _fetch_bot_guilds(bot_token: str) -> dict[int, BotGuildMeta]:
    headers = {"Authorization": f"Bot {bot_token}"}
    meta: dict[int, BotGuildMeta] = {}
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
                meta[int(guild["id"])] = BotGuildMeta(
                    name=guild.get("name", ""), icon=guild.get("icon")
                )
            if len(batch) < 200:
                break
            after = batch[-1]["id"]
    return meta


class BotGuildsCache:
    def __init__(self, bot_token: str, ttl_seconds: int = 300):
        self._token = bot_token
        self._ttl = ttl_seconds
        self._meta: dict[int, BotGuildMeta] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> set[int]:
        """id серверов, где стоит бот."""
        return set(await self.get_meta())

    async def get_meta(self) -> dict[int, BotGuildMeta]:
        """id -> имя/иконка серверов бота (для операторского списка в панели)."""
        if self._fresh():
            return self._meta
        async with self._lock:
            if self._fresh():  # мог обновиться, пока ждали лок
                return self._meta
            self._meta = await _fetch_bot_guilds(self._token)
            self._fetched_at = time.monotonic()
            return self._meta

    def _fresh(self) -> bool:
        return self._fetched_at > 0.0 and (time.monotonic() - self._fetched_at) < self._ttl

    def prime(self, ids: set[int] | dict[int, BotGuildMeta]) -> None:
        """Подставить список напрямую (для тестов — без похода в Discord).
        Принимает и голый набор id, и готовую мету id->BotGuildMeta."""
        if isinstance(ids, dict):
            self._meta = dict(ids)
        else:
            self._meta = {gid: BotGuildMeta(name="", icon=None) for gid in ids}
        self._fetched_at = time.monotonic()
