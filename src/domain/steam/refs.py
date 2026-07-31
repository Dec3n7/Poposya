"""Разбор ввода игры Steam в appid и детерминированные URL по appid.

Принимает голый appid (`730`) или ссылку на магазин
(`https://store.steampowered.com/app/730/Counter-Strike_2/`). Чистая доменная
логика — без сети."""

import re

_APP_URL_RE = re.compile(r"store\.steampowered\.com/app/(\d+)", re.IGNORECASE)


def parse_app_ref(text: str) -> int | None:
    """appid или None, если ввод не похож на игру Steam."""
    if not text:
        return None
    ref = text.strip()
    if ref.isdigit():
        appid = int(ref)
        return appid if appid > 0 else None
    match = _APP_URL_RE.search(ref)
    return int(match.group(1)) if match else None


def store_url(appid: int) -> str:
    return f"https://store.steampowered.com/app/{appid}"


def header_url(appid: int) -> str:
    """Стабильный URL header-картинки игры по appid (для запасной иллюстрации,
    когда у самой новости своей картинки нет)."""
    return f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"
