"""Тонкие HTTP-обёртки панели над Discord REST + OAuth: обмен кода, кто
пользователь и чем он вправе управлять, участники/каналы/пользователи сервера,
кэш серверов бота. Сеть замокана: aiohttp.ClientSession подменяется фейком,
возвращающим заранее заготовленные ответы (или бросающим ClientError)."""

import aiohttp
import pytest

from src.api import bot_guilds as bot_guilds_module
from src.api import discord_guild as guild_module
from src.api import discord_members as members_module
from src.api import discord_oauth as oauth_module
from src.api import discord_users as users_module
from src.api.bot_guilds import BotGuildMeta, BotGuildsCache, _fetch_bot_guilds
from src.api.discord_guild import fetch_guild_channels
from src.api.discord_members import fetch_guild_members
from src.api.discord_oauth import (
    OAuthError,
    _can_manage,
    authorize_url,
    exchange_code,
    fetch_identity,
    manageable_guilds,
)
from src.api.discord_users import (
    avatar_url,
    fetch_users,
    guild_icon_url,
)

# --- фейк aiohttp -----------------------------------------------------------


class _Resp:
    def __init__(self, status=200, json_data=None):
        self.status = status
        self._json = json_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._json


class _FakeSession:
    """Async-контекст ClientSession. Ответы берутся из очереди по порядку, либо
    из router(url). Элемент-исключение бросается синхронно (как настоящий
    aiohttp при сетевом сбое) — код обёрток это ловит."""

    def __init__(self, queue=None, router=None):
        self._queue = list(queue or [])
        self._router = router
        self.requests: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _resolve(self, url):
        item = self._router(url) if self._router is not None else self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, params=None, headers=None):
        self.requests.append(("GET", url, params, headers))
        return self._resolve(url)

    def post(self, url, data=None, headers=None):
        self.requests.append(("POST", url, data, headers))
        return self._resolve(url)

    async def close(self):
        pass


def _patch(monkeypatch, module, session):
    monkeypatch.setattr(f"{module.__name__}.aiohttp.ClientSession", lambda **kw: session)
    return session


# ===========================================================================
# discord_oauth
# ===========================================================================


def test_authorize_url_contains_all_params():
    url = authorize_url("cid123", "https://panel/cb", "state-xyz")
    assert url.startswith("https://discord.com/oauth2/authorize?")
    assert "client_id=cid123" in url
    assert "state=state-xyz" in url
    assert "scope=identify+guilds" in url
    assert "prompt=none" in url


def test_can_manage_owner():
    assert _can_manage({"owner": True, "permissions": "0"}) is True


def test_can_manage_manage_guild_bit():
    assert _can_manage({"permissions": str(0x20)}) is True


def test_can_manage_administrator_bit():
    assert _can_manage({"permissions": str(0x8)}) is True


def test_can_manage_without_rights():
    assert _can_manage({"permissions": "0"}) is False


def test_can_manage_bad_permissions_value():
    assert _can_manage({"permissions": "не число"}) is False
    assert _can_manage({"permissions": None}) is False


def test_manageable_guilds_filters_and_maps():
    guilds = [
        {"id": "1", "name": "Мой", "icon": "abc", "owner": True},
        {"id": "2", "name": "Управляю", "permissions": str(0x20)},
        {"id": "3", "name": "Просто участник", "permissions": "0"},
    ]
    result = manageable_guilds(guilds)
    assert [g.id for g in result] == [1, 2]
    assert result[0].name == "Мой" and result[0].icon == "abc"
    assert result[1].icon is None  # icon отсутствует -> None


async def test_exchange_code_success(monkeypatch):
    session = _FakeSession(queue=[_Resp(200, {"access_token": "tok-123"})])
    _patch(monkeypatch, oauth_module, session)
    token = await exchange_code("cid", "secret", "the-code", "https://panel/cb")
    assert token == "tok-123"
    method, url, data, _headers = session.requests[0]
    assert method == "POST"
    assert data["code"] == "the-code"
    assert data["grant_type"] == "authorization_code"


async def test_exchange_code_http_error(monkeypatch):
    session = _FakeSession(queue=[_Resp(400, None)])
    _patch(monkeypatch, oauth_module, session)
    with pytest.raises(OAuthError, match="token exchange failed"):
        await exchange_code("cid", "secret", "bad", "https://panel/cb")


async def test_exchange_code_missing_token(monkeypatch):
    session = _FakeSession(queue=[_Resp(200, {"token_type": "Bearer"})])
    _patch(monkeypatch, oauth_module, session)
    with pytest.raises(OAuthError, match="no access_token"):
        await exchange_code("cid", "secret", "code", "https://panel/cb")


async def test_fetch_identity_success(monkeypatch):
    user = {"id": "42", "username": "user"}
    guilds = [{"id": "1", "name": "G"}]
    session = _FakeSession(queue=[_Resp(200, user), _Resp(200, guilds)])
    _patch(monkeypatch, oauth_module, session)
    got_user, got_guilds = await fetch_identity("access-token")
    assert got_user == user
    assert got_guilds == guilds


async def test_fetch_identity_user_request_fails(monkeypatch):
    session = _FakeSession(queue=[_Resp(401, None)])
    _patch(monkeypatch, oauth_module, session)
    with pytest.raises(OAuthError, match="fetch user failed"):
        await fetch_identity("bad-token")


async def test_fetch_identity_guilds_request_fails(monkeypatch):
    session = _FakeSession(queue=[_Resp(200, {"id": "42"}), _Resp(500, None)])
    _patch(monkeypatch, oauth_module, session)
    with pytest.raises(OAuthError, match="fetch guilds failed"):
        await fetch_identity("token")


# ===========================================================================
# discord_members
# ===========================================================================


async def test_fetch_members_skips_bots_and_resolves_name(monkeypatch):
    batch = [
        {"user": {"id": "1", "username": "human", "global_name": "Человек"}, "nick": "Ник"},
        {"user": {"id": "2", "username": "botuser", "bot": True}},  # бот — пропускаем
        {"user": {"id": "3", "username": "onlylogin"}},  # без nick/global_name — фолбэк
    ]
    session = _FakeSession(queue=[_Resp(200, batch)])  # < _PAGE -> одна страница
    _patch(monkeypatch, members_module, session)
    result = await fetch_guild_members("bot-token", 10)
    assert [m["user_id"] for m in result] == [1, 3]
    assert result[0]["name"] == "Ник"  # nick приоритетнее
    assert result[1]["name"] == "onlylogin"  # фолбэк на username


async def test_fetch_members_paginates_with_after_cursor(monkeypatch):
    monkeypatch.setattr(members_module, "_PAGE", 2)  # маленькая страница для теста
    page1 = [
        {"user": {"id": "10", "username": "a"}},
        {"user": {"id": "20", "username": "b"}},
    ]
    page2 = [{"user": {"id": "30", "username": "c"}}]  # < _PAGE -> последняя
    session = _FakeSession(queue=[_Resp(200, page1), _Resp(200, page2)])
    _patch(monkeypatch, members_module, session)
    result = await fetch_guild_members("bot-token", 10)
    assert [m["user_id"] for m in result] == [10, 20, 30]
    # курсор after второй страницы = id последнего из первой (20)
    assert session.requests[1][2]["after"] == 20


async def test_fetch_members_respects_cap(monkeypatch):
    monkeypatch.setattr(members_module, "_PAGE", 2)
    page1 = [
        {"user": {"id": "10", "username": "a"}},
        {"user": {"id": "20", "username": "b"}},
    ]
    session = _FakeSession(queue=[_Resp(200, page1)])
    _patch(monkeypatch, members_module, session)
    result = await fetch_guild_members("bot-token", 10, cap=1)
    assert len(result) == 2  # одна страница добралась, но цикл дальше не пошёл (cap)
    assert len(session.requests) == 1


async def test_fetch_members_http_error_breaks(monkeypatch):
    session = _FakeSession(queue=[_Resp(403, None)])
    _patch(monkeypatch, members_module, session)
    assert await fetch_guild_members("bot-token", 10) == []


async def test_fetch_members_client_error_breaks(monkeypatch):
    session = _FakeSession(queue=[aiohttp.ClientError("network down")])
    _patch(monkeypatch, members_module, session)
    assert await fetch_guild_members("bot-token", 10) == []


async def test_fetch_members_empty_batch_breaks(monkeypatch):
    session = _FakeSession(queue=[_Resp(200, [])])
    _patch(monkeypatch, members_module, session)
    assert await fetch_guild_members("bot-token", 10) == []


# ===========================================================================
# discord_guild
# ===========================================================================


async def test_fetch_channels_groups_filters_and_sorts(monkeypatch):
    data = [
        {"id": "1", "name": "голос", "type": 2, "position": 1},
        {"id": "2", "name": "текст-б", "type": 0, "position": 2},
        {"id": "3", "name": "текст-а", "type": 0, "position": 1},
        {"id": "4", "name": "тред", "type": 11, "position": 0},  # неизвестный тип — выкинут
    ]
    session = _FakeSession(queue=[_Resp(200, data)])
    _patch(monkeypatch, guild_module, session)
    channels = await fetch_guild_channels("bot-token", 10)
    assert [c["id"] for c in channels] == ["1", "3", "2"]  # Голосовые, затем Текстовые по позиции
    assert channels[0]["group"] == "Голосовые"
    assert all("тред" != c["name"] for c in channels)  # тип 11 не попал


async def test_fetch_channels_http_error_raises(monkeypatch):
    session = _FakeSession(queue=[_Resp(500, None)])
    _patch(monkeypatch, guild_module, session)
    with pytest.raises(OAuthError, match="channels fetch failed"):
        await fetch_guild_channels("bot-token", 10)


# ===========================================================================
# discord_users
# ===========================================================================


@pytest.fixture(autouse=True)
def _clear_user_cache():
    users_module._USER_CACHE.clear()
    yield
    users_module._USER_CACHE.clear()


def test_avatar_url_variants():
    assert avatar_url(1, None) is None
    assert avatar_url(1, "hash") == "https://cdn.discordapp.com/avatars/1/hash.png?size=64"


def test_guild_icon_url_variants():
    assert guild_icon_url(5, None) is None
    assert guild_icon_url(5, "ic") == "https://cdn.discordapp.com/icons/5/ic.png?size=64"


async def test_fetch_users_empty_ids_returns_empty():
    assert await fetch_users("bot-token", []) == {}


async def test_fetch_users_resolves_and_caches(monkeypatch):
    def router(url):
        uid = url.rsplit("/", 1)[-1]
        return _Resp(200, {"id": uid, "global_name": f"Имя{uid}", "avatar": "av"})

    session = _FakeSession(router=router)
    _patch(monkeypatch, users_module, session)
    result = await fetch_users("bot-token", [100, 200])
    assert result[100]["username"] == "Имя100"
    assert result[200]["avatar"].endswith("/avatars/200/av.png?size=64")

    # второй вызов — всё из кэша, сессия больше не нужна
    def boom(url):
        raise AssertionError("не должно ходить в сеть — есть кэш")

    _patch(monkeypatch, users_module, _FakeSession(router=boom))
    cached = await fetch_users("bot-token", [100, 200])
    assert cached[100]["username"] == "Имя100"


async def test_fetch_users_falls_back_to_username(monkeypatch):
    def router(url):
        uid = url.rsplit("/", 1)[-1]
        return _Resp(200, {"id": uid, "username": "логин", "avatar": None})

    _patch(monkeypatch, users_module, _FakeSession(router=router))
    result = await fetch_users("bot-token", [100])
    assert result[100]["username"] == "логин"
    assert result[100]["avatar"] is None


async def test_fetch_users_http_error_gives_none_and_not_cached(monkeypatch):
    _patch(monkeypatch, users_module, _FakeSession(router=lambda url: _Resp(404, None)))
    result = await fetch_users("bot-token", [100])
    assert result[100] == {"username": None, "avatar": None}
    assert 100 not in users_module._USER_CACHE  # неуспех не кэшируем


async def test_fetch_users_client_error_gives_none(monkeypatch):
    _patch(
        monkeypatch,
        users_module,
        _FakeSession(router=lambda url: aiohttp.ClientError("down")),
    )
    result = await fetch_users("bot-token", [100])
    assert result[100]["username"] is None


async def test_fetch_users_all_cached_skips_session(monkeypatch):
    import time

    users_module._USER_CACHE[100] = (time.monotonic(), {"username": "Кэш", "avatar": None})

    def boom(**kw):
        raise AssertionError("сессия не должна создаваться")

    monkeypatch.setattr(users_module.aiohttp, "ClientSession", boom)
    result = await fetch_users("bot-token", [100])
    assert result[100]["username"] == "Кэш"


# ===========================================================================
# bot_guilds
# ===========================================================================


async def test_fetch_bot_guilds_paginates(monkeypatch):
    page1 = [{"id": str(i)} for i in range(200)]  # ровно лимит -> будет вторая страница
    page2 = [{"id": "900", "name": "Финал", "icon": "ic"}]  # < 200 -> последняя
    session = _FakeSession(queue=[_Resp(200, page1), _Resp(200, page2)])
    _patch(monkeypatch, bot_guilds_module, session)
    meta = await _fetch_bot_guilds("bot-token")
    assert 900 in meta and 0 in meta and 199 in meta
    assert len(meta) == 201
    assert meta[900] == BotGuildMeta(name="Финал", icon="ic")  # имя/иконка сохранены
    # курсор after второй страницы = id последнего элемента первой
    assert session.requests[1][2]["after"] == "199"


async def test_fetch_bot_guilds_http_error_raises(monkeypatch):
    session = _FakeSession(queue=[_Resp(401, None)])
    _patch(monkeypatch, bot_guilds_module, session)
    with pytest.raises(OAuthError, match="bot guilds fetch failed"):
        await _fetch_bot_guilds("bot-token")


async def test_fetch_bot_guilds_empty(monkeypatch):
    session = _FakeSession(queue=[_Resp(200, [])])
    _patch(monkeypatch, bot_guilds_module, session)
    assert await _fetch_bot_guilds("bot-token") == {}


async def test_cache_prime_serves_without_network():
    cache = BotGuildsCache("bot-token")
    cache.prime({1, 2, 3})
    assert await cache.get() == {1, 2, 3}


async def test_cache_fetches_when_cold_then_reuses(monkeypatch):
    session = _FakeSession(queue=[_Resp(200, [{"id": "7"}, {"id": "8"}])])
    _patch(monkeypatch, bot_guilds_module, session)
    cache = BotGuildsCache("bot-token", ttl_seconds=300)
    first = await cache.get()
    assert first == {7, 8}
    # второй get() в пределах TTL — сеть не трогаем (одна пачка запросов всего)
    again = await cache.get()
    assert again == {7, 8}
    assert len(session.requests) == 1


async def test_cache_refetches_when_stale(monkeypatch):
    session = _FakeSession(queue=[_Resp(200, [{"id": "7"}])])
    _patch(monkeypatch, bot_guilds_module, session)
    cache = BotGuildsCache("bot-token", ttl_seconds=0)  # мгновенно протухает
    assert await cache.get() == {7}
    session2 = _FakeSession(queue=[_Resp(200, [{"id": "9"}])])
    _patch(monkeypatch, bot_guilds_module, session2)
    assert await cache.get() == {9}  # протухло -> сходили снова


async def test_cache_concurrent_get_fetches_once(monkeypatch):
    import asyncio

    # Один запрос на двоих: пока первый ждёт сеть (yield через sleep(0)), второй
    # успевает зайти и повиснуть на локе; после — видит уже свежий кэш (двойная
    # проверка внутри лока) и в сеть не идёт.
    calls = 0

    async def fake_fetch(token):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)  # уступаем — даём второму дойти до лока
        return {5: BotGuildMeta(name="", icon=None)}

    monkeypatch.setattr(bot_guilds_module, "_fetch_bot_guilds", fake_fetch)
    cache = BotGuildsCache("bot-token", ttl_seconds=300)
    a, b = await asyncio.gather(cache.get(), cache.get())
    assert a == b == {5}
    assert calls == 1  # сходили в сеть ровно раз (второй взял из кэша под локом)
