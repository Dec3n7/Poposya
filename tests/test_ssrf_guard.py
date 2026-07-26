"""SSRF-щит загрузчика картинок профиля бота (аватар/баннер по URL).

Ядро — чистая проверка адреса `_is_public_ip` (без сети). Плюс `_assert_public_url`
с подменённым резолвом: хост, ведущий во внутреннюю сеть, должен отбиваться.
"""

import socket

import pytest

from src.infrastructure.commands.bridge import CommandError
from src.infrastructure.discord.command_executor import _assert_public_url, _is_public_ip


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # private A
        "172.16.0.1",  # private B
        "192.168.1.1",  # private C
        "169.254.169.254",  # link-local (облачный metadata-эндпоинт)
        "0.0.0.0",  # unspecified
        "100.64.0.1",  # CGNAT (не global)
        "::1",  # IPv6 loopback
        "fe80::1",  # IPv6 link-local
        "fc00::1",  # IPv6 unique-local
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:10.0.0.1",  # IPv4-mapped private
        "не-адрес",  # мусор
    ],
)
def test_is_public_ip_blocks_internal(ip):
    assert _is_public_ip(ip) is False


@pytest.mark.parametrize(
    "ip",
    ["93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"],
)
def test_is_public_ip_allows_public(ip):
    assert _is_public_ip(ip) is True


def _patch_resolver(monkeypatch, host_to_ips):
    """loop.getaddrinfo делегирует в socket.getaddrinfo — подменяем его."""

    def fake(host, *_a, **_k):
        ips = host_to_ips.get(host, [])
        if not ips:
            raise socket.gaierror("name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


async def test_assert_public_url_allows_public(monkeypatch):
    _patch_resolver(monkeypatch, {"cdn.example.com": ["93.184.216.34"]})
    # не бросает
    await _assert_public_url("https://cdn.example.com/pic.png")


async def test_assert_public_url_blocks_private(monkeypatch):
    _patch_resolver(monkeypatch, {"internal.local": ["10.0.0.7"]})
    with pytest.raises(CommandError, match="внутреннюю сеть"):
        await _assert_public_url("http://internal.local/probe")


async def test_assert_public_url_blocks_literal_metadata_ip(monkeypatch):
    # literal-IP резолвится сам в себя — проверяем отбой облачного metadata-адреса
    _patch_resolver(monkeypatch, {"169.254.169.254": ["169.254.169.254"]})
    with pytest.raises(CommandError, match="внутреннюю сеть"):
        await _assert_public_url("http://169.254.169.254/latest/meta-data/")


async def test_assert_public_url_rejects_mixed_public_and_private(monkeypatch):
    # хотя бы один приватный адрес среди ответов резолва -> отбой (консервативно)
    _patch_resolver(monkeypatch, {"split.example": ["93.184.216.34", "127.0.0.1"]})
    with pytest.raises(CommandError, match="внутреннюю сеть"):
        await _assert_public_url("http://split.example/x")


async def test_assert_public_url_unresolvable(monkeypatch):
    _patch_resolver(monkeypatch, {})  # любой хост -> gaierror
    with pytest.raises(CommandError, match="разрешить адрес"):
        await _assert_public_url("http://nope.invalid/x")
