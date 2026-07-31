"""Разбор пользовательского ввода репозитория в пару (owner, name).

Принимает `owner/name`, полный URL `https://github.com/owner/name(.git)`,
`github.com/owner/name` и хвосты вроде `/tree/main`. Чистая доменная логика
формата ссылки GitHub — без сети."""

import re

# GitHub: владелец — буквы/цифры/дефис; имя репозитория дополнительно допускает
# точку и подчёркивание. Длины с запасом (реальные лимиты — 39 и 100).
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def parse_repo_ref(text: str) -> tuple[str, str] | None:
    """(owner, name) или None, если ввод не похож на ссылку на репозиторий."""
    if not text:
        return None
    ref = text.strip()
    # срезаем схему и хост, если дали URL
    ref = re.sub(r"^(https?://)?(www\.)?github\.com/", "", ref, flags=re.IGNORECASE)
    ref = ref.strip("/")
    parts = ref.split("/")
    if len(parts) < 2:
        return None
    owner, name = parts[0], parts[1]
    if name.endswith(".git"):
        name = name[:-4]
    if name == "." or name == "..":
        return None
    if not _OWNER_RE.match(owner) or not _NAME_RE.match(name):
        return None
    return owner, name
