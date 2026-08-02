"""SSRF-щит: хост URL не должен резолвиться во внутреннюю сеть.

Общий для двух мест, где мы тянем URL, пришедший от пользователя:
- загрузка аватара/баннера бота по ссылке (командный мост, `command_executor`);
- резолв ссылки в `/play` через yt-dlp (`ytdlp_source`).

Ядро — чистая проверка адреса `is_public_ip` (без сети). `assert_public_url`
резолвит хост и требует, чтобы ВСЕ адреса были публичными; кидает `SsrfError`,
а вызывающий переводит её в своё доменное исключение (CommandError/TrackResolveError).
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse


class SsrfError(Exception):
    """Хост URL ведёт во внутреннюю сеть, не резолвится или URL без хоста."""


def is_public_ip(ip: str) -> bool:
    """Адрес НЕ ведёт во внутреннюю сеть? loopback/private/link-local/reserved/
    multicast/unspecified считаем небезопасными (SSRF во внутреннюю сеть)."""
    try:
        addr: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    # ::ffff:10.0.0.1 и подобное — проверяем встроенный IPv4-адрес
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    # not is_global ловит и то, что не покрыто явными флагами (напр. CGNAT
    # 100.64.0.0/10, TEST-NET) — «глобально маршрутизируемый?» и есть критерий.
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
        or not addr.is_global
    )


async def assert_public_url(url: str) -> None:
    """SSRF-щит: хост URL не должен резолвиться во внутреннюю сеть. Проверяем ВСЕ
    адреса при резолве — отсекаются и literal-IP (127.0.0.1, 169.254.169.254,
    10.x, [::1]), и обфускация (http://2130706433/), и домены, указывающие
    внутрь. Остаточный риск DNS-rebinding (повторный резолв на connect вернёт
    другой адрес) приемлем. Кидает `SsrfError`."""
    host = urlparse(url).hostname
    if not host:
        raise SsrfError("В URL нет хоста.")
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SsrfError("Не удалось разрешить адрес.") from exc
    if not infos or not all(is_public_ip(info[4][0]) for info in infos):
        raise SsrfError("Адрес ведёт во внутреннюю сеть — запрещено.")
