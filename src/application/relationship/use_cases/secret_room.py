"""Секретная комната: выдача одноразового ключа, его проверка, регистрация
созданной комнаты (пометить ключ использованным) и сбор истёкших комнат."""

import secrets as _secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from src.domain.relationship.entities import SecretCode, SecretRoom

from ._common import UowFactory


class IssueSecretCodeUseCase:
    """Выдаёт ключ секретной комнаты (или возвращает уже выданный
    неиспользованный)."""

    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int, now: datetime) -> str:
        async with self._uow_factory() as uow:
            existing = await uow.secret_rooms.get_code(user_id, guild_id)
            if existing is not None and existing.used_at is None:
                return existing.code
            # 64 бита (было 32): подбор через /secret и так упирался в rate-limit
            # Discord, но запас лишним не будет. Формат - 4 группы по 4 для читаемости.
            raw = _secrets.token_hex(8).upper()
            code = "-".join(raw[i : i + 4] for i in range(0, 16, 4))
            await uow.secret_rooms.save_code(
                SecretCode(guild_id=guild_id, user_id=user_id, code=code, issued_at=now)
            )
            await uow.commit()
            return code


@dataclass(frozen=True)
class RedeemCheck:
    ok: bool
    reason: str = ""  # no_code | used | wrong | room_active
    active_room_channel_id: int | None = None


class ValidateSecretCodeUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(
        self, user_id: int, guild_id: int, code_input: str, now: datetime
    ) -> RedeemCheck:
        async with self._uow_factory() as uow:
            room = await uow.secret_rooms.get_active_room(guild_id, now)
            if room is not None:
                return RedeemCheck(False, "room_active", room.text_channel_id)
            stored = await uow.secret_rooms.get_code(user_id, guild_id)
            if stored is None:
                return RedeemCheck(False, "no_code")
            if stored.used_at is not None:
                return RedeemCheck(False, "used")
            # constant-time сравнение: подбор по таймингу через Discord и так
            # непрактичен, но правильная привычка для секретов дешёвая
            if not _secrets.compare_digest(stored.code.upper(), code_input.strip().upper()):
                return RedeemCheck(False, "wrong")
            return RedeemCheck(True)


class RegisterSecretRoomUseCase:
    """Помечает ключ использованным и записывает комнату (после того, как
    ког создал каналы в Discord)."""

    def __init__(self, uow_factory: UowFactory, hours: int, settings_provider=None):
        self._uow_factory = uow_factory
        self._hours = hours
        self._settings = settings_provider

    async def execute(
        self,
        user_id: int,
        guild_id: int,
        text_channel_id: int,
        voice_channel_id: int,
        now: datetime,
    ) -> datetime:
        hours = self._hours
        if self._settings is not None:
            hours = self._settings.resolved(guild_id).secret_room_hours
        expires_at = now + timedelta(hours=hours)
        async with self._uow_factory() as uow:
            stored = await uow.secret_rooms.get_code(user_id, guild_id)
            if stored is not None:
                await uow.secret_rooms.save_code(replace(stored, used_at=now))
            await uow.secret_rooms.add_room(
                SecretRoom(
                    guild_id=guild_id,
                    text_channel_id=text_channel_id,
                    voice_channel_id=voice_channel_id,
                    expires_at=expires_at,
                    created_by=user_id,
                )
            )
            await uow.commit()
            return expires_at


class GetSecretCodeUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, user_id: int, guild_id: int) -> SecretCode | None:
        async with self._uow_factory() as uow:
            return await uow.secret_rooms.get_code(user_id, guild_id)


class PopExpiredSecretRoomsUseCase:
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def execute(self, now: datetime) -> list[SecretRoom]:
        async with self._uow_factory() as uow:
            expired = await uow.secret_rooms.pop_expired_rooms(now)
            await uow.commit()
            return expired
