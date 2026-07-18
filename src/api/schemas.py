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
