from dataclasses import dataclass
from datetime import datetime


@dataclass
class BotProfile:
    """Пер-серверный профиль бота: ник и URL картинок аватара/баннера. Пустая
    строка = использовать глобальный профиль (сброс). Хранится для показа/правки
    в панели; применяет бот через мост (guild.me.edit)."""

    guild_id: int
    nick: str = ""
    avatar_url: str = ""
    banner_url: str = ""
    # загруженный+обрезанный аватар (base64 data-URL); приоритетнее avatar_url
    avatar_data: str = ""
    # загруженный+обрезанный баннер (base64 data-URL); приоритетнее banner_url
    banner_data: str = ""
    updated_at: datetime | None = None
