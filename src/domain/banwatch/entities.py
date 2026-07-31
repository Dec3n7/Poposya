from dataclasses import dataclass
from datetime import datetime


@dataclass
class ServerBan:
    """Бан пользователя на конкретном сервере, где стоит бот. Собирается со ВСЕХ
    серверов (не только баны самого бота — любые, поставленные админами), чтобы
    показать модератору кросс-серверную картину. Причина — как её видит Discord
    (может быть пустой). Наружу в Discord НЕ публикуется — только веб-панель."""

    user_id: int
    guild_id: int
    guild_name: str = ""
    reason: str = ""
    banned_at: datetime | None = None
    id: int | None = None
