"""Клиент Discord OAuth2 и минимум Discord API (aiohttp — уже в зависимостях).

Только то, что нужно панели: обмен кода на токен, кто пользователь и какими
серверами он вправе управлять. Идентификация — по Discord ID, своих паролей нет.
"""

from urllib.parse import urlencode

import aiohttp

from src.api.security import SessionGuild

DISCORD_API = "https://discord.com/api/v10"
AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = f"{DISCORD_API}/oauth2/token"
SCOPE = "identify guilds"

# биты прав Discord: право «Управление сервером» или администратор = можно
# настраивать сервер в панели
_MANAGE_GUILD = 0x20
_ADMINISTRATOR = 0x8


class OAuthError(Exception):
    """Обмен кода/запрос к Discord не удался."""


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
            "prompt": "none",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def _can_manage(guild: dict) -> bool:
    if guild.get("owner"):
        return True
    try:
        perms = int(guild.get("permissions", 0))
    except (TypeError, ValueError):
        return False
    return bool(perms & (_MANAGE_GUILD | _ADMINISTRATOR))


def manageable_guilds(guilds: list[dict]) -> list[SessionGuild]:
    """Из списка серверов пользователя — только те, где он может управлять."""
    return [
        SessionGuild(id=int(g["id"]), name=g.get("name", ""), icon=g.get("icon"))
        for g in guilds
        if _can_manage(g)
    ]


async def exchange_code(
    client_id: str, client_secret: str, code: str, redirect_uri: str
) -> str:
    """Код авторизации -> access_token пользователя."""
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(TOKEN_URL, data=data) as resp:
            if resp.status != 200:
                raise OAuthError(f"token exchange failed: HTTP {resp.status}")
            payload = await resp.json()
    token = payload.get("access_token")
    if not token:
        raise OAuthError("no access_token in response")
    return token


async def fetch_identity(access_token: str) -> tuple[dict, list[dict]]:
    """(пользователь, его серверы) по access_token."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(f"{DISCORD_API}/users/@me") as resp:
            if resp.status != 200:
                raise OAuthError(f"fetch user failed: HTTP {resp.status}")
            user = await resp.json()
        async with session.get(f"{DISCORD_API}/users/@me/guilds") as resp:
            if resp.status != 200:
                raise OAuthError(f"fetch guilds failed: HTTP {resp.status}")
            guilds = await resp.json()
    return user, guilds
