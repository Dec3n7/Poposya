"""API пула ключей (оператор): выпуск, обзор, сами ключи, отзыв/реактивация,
экспорт. Доступ — только require_operator; серверный админ/аноним — 403/401."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.container import assemble_container
from src.api.security import SESSION_COOKIE, Session, SessionGuild, encode_session
from src.config import Settings

GUILD = 10
OPERATOR = 1
STRANGER = 2
KEY_SECRET = "api-key-signing-secret-at-least-32-bytes!!"
BASE = "/api/admin/premium-keys"


def make_settings(**over):
    base = {
        "discord_token": "t",
        "discord_client_id": "cid",
        "discord_client_secret": "csec",
        "web_session_secret": "test-session-secret-at-least-32-bytes!!",
        "web_operator_ids": [OPERATOR],
        "key_signing_secret": KEY_SECRET,
    }
    base.update(over)
    return Settings(_env_file=None, **base)


@pytest.fixture
async def container(session_factory):
    c = assemble_container(make_settings(), session_factory.kw["bind"], session_factory)
    c.bot_guilds.prime({GUILD})
    await c.entitlements.load_all()
    return c


def _cookie(settings, user_id: int) -> str:
    session = Session(
        user_id=user_id,
        username="u",
        avatar=None,
        guilds=[SessionGuild(id=GUILD, name="G", icon=None)],
    )
    return encode_session(settings.web_session_secret, session, 24, settings.web_session_version)


@pytest.fixture
async def client(container):
    app = create_app(container)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE: _cookie(container.settings, OPERATOR)},
    ) as c:
        yield c


async def _mint(client, tier="premium", duration=30, count=2, label="boosty"):
    r = await client.post(
        f"{BASE}/batches",
        json={"tier": tier, "duration_days": duration, "count": count, "label": label},
    )
    assert r.status_code == 200, r.text
    return r.json()


# --- доступ ------------------------------------------------------------------


async def test_anon_401(container):
    app = create_app(container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get(BASE)).status_code == 401


async def test_stranger_403(container):
    app = create_app(container)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE: _cookie(container.settings, STRANGER)},
    ) as stranger:
        assert (await stranger.get(BASE)).status_code == 403
        assert (
            await stranger.post(
                f"{BASE}/batches",
                json={"tier": "premium", "duration_days": 30, "count": 1, "label": "x"},
            )
        ).status_code == 403


# --- выпуск и обзор ----------------------------------------------------------


async def test_overview_enabled_and_durations(client):
    r = await client.get(BASE)
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["durations"] == [30, 90, 180, 365]
    assert body["batches"] == [] and body["skus"] == []


async def test_mint_appears_in_overview_and_sku(client):
    minted = await _mint(client, tier="pro", duration=90, count=3)
    assert len(minted["keys"]) == 3
    body = (await client.get(BASE)).json()
    assert len(body["batches"]) == 1
    b = body["batches"][0]
    assert b["tier"] == "pro" and b["duration_days"] == 90 and b["issued"] == 3
    assert b["seats"] == 5 and b["capacity"] == 15
    sku = next(s for s in body["skus"] if s["tier"] == "pro" and s["duration_days"] == 90)
    assert sku["issued"] == 3 and sku["remaining"] == 15


async def test_mint_rejects_bad_duration_and_tier(client):
    assert (
        await client.post(
            f"{BASE}/batches",
            json={"tier": "premium", "duration_days": 45, "count": 1, "label": "x"},
        )
    ).status_code == 422
    assert (
        await client.post(
            f"{BASE}/batches",
            json={"tier": "gold", "duration_days": 30, "count": 1, "label": "x"},
        )
    ).status_code == 422


# --- сами ключи, экспорт -----------------------------------------------------


async def test_batch_keys_show_actual_keys_and_status(client):
    minted = await _mint(client, count=2)
    r = await client.get(f"{BASE}/batches/{minted['batch_id']}/keys")
    assert r.status_code == 200
    keys = r.json()
    assert sorted(k["key"] for k in keys) == sorted(minted["keys"])  # перевыпуск == исходные
    assert all(k["status"] == "unredeemed" for k in keys)


async def test_export_plaintext(client):
    minted = await _mint(client, count=3)
    r = await client.get(f"{BASE}/batches/{minted['batch_id']}/export")
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    assert sorted(lines) == sorted(minted["keys"])


async def test_batch_keys_404_missing(client):
    assert (await client.get(f"{BASE}/batches/999/keys")).status_code == 404


# --- отзыв / реактивация -----------------------------------------------------


async def test_soft_revoke_and_reactivate(client):
    minted = await _mint(client)
    bid = minted["batch_id"]
    r = await client.post(f"{BASE}/batches/{bid}/revoke", json={"reason": "leak"})
    assert r.status_code == 200 and r.json()["guilds_stripped"] == 0
    assert (await client.get(BASE)).json()["batches"][0]["revoked"] is True
    r2 = await client.post(f"{BASE}/batches/{bid}/reactivate")
    assert r2.status_code == 200 and r2.json()["reactivated"] is True
    assert (await client.get(BASE)).json()["batches"][0]["revoked"] is False


async def test_revoke_requires_reason(client):
    minted = await _mint(client)
    r = await client.post(f"{BASE}/batches/{minted['batch_id']}/revoke", json={})
    assert r.status_code == 422  # reason обязателен


async def test_revoke_404_missing(client):
    assert (
        await client.post(f"{BASE}/batches/999/revoke", json={"reason": "x"})
    ).status_code == 404
