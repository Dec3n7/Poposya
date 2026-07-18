"""DTO ответов API (Pydantic). ID Discord — строками: snowflake больше 2^53 и
в JS-числе теряет точность."""

from pydantic import BaseModel


class GuildDTO(BaseModel):
    id: str
    name: str
    icon: str | None = None


class MeDTO(BaseModel):
    user_id: str
    username: str
    avatar: str | None = None
    guilds: list[GuildDTO]  # серверы, где пользователь может управлять


class SettingFieldDTO(BaseModel):
    """Одна настройка сервера: описание поля + текущее значение. Фронт строит по
    этому форму, не хардкодя поля. channel-значения — строками (snowflake > 2^53)."""

    key: str
    label: str
    kind: str  # bool | channel | float | int
    unit: str = ""
    min: float | None = None
    max: float | None = None
    default: bool | int | float | str  # глобальный дефолт из .env
    value: bool | int | float | str  # действующее значение (override или дефолт)
    is_override: bool  # переопределено на этом сервере


class SettingUpdate(BaseModel):
    # значение приходит как есть (строка/число/булево); бэкенд валидирует и парсит
    value: bool | int | float | str
