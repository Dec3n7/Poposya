"""Пер-серверные настройки в вебе — тонкая обёртка над `GuildSettingsService`.

Никакой своей бизнес-логики: валидация (поле + кросс-поле), сохранение и рассылка
`pg_notify` (бот подхватит без рестарта) уже внутри сервиса — того же, что и у
`/config` в Discord. Список редактируемых полей и их типы — из `SETTING_SPECS`.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import get_container, require_guild_manager
from src.api.schemas import SettingFieldDTO, SettingUpdate
from src.infrastructure.guild_settings import SETTING_SPECS, GuildSettingsService

router = APIRouter(prefix="/api/guilds/{guild_id}/settings", tags=["settings"])


def _field(service: GuildSettingsService, guild_id: int, key: str) -> SettingFieldDTO:
    spec = SETTING_SPECS[key]
    default = service.default(key)
    value = service.current(guild_id, key)
    if spec.kind == "bool":  # override хранится как 0/1 -> приводим к булеву
        default, value = bool(default), bool(value)
    elif spec.kind == "channel":  # id канала — snowflake, отдаём строкой
        default, value = str(default), str(value)
    return SettingFieldDTO(
        key=key,
        label=spec.label,
        kind=spec.kind,
        unit=spec.unit,
        min=spec.min,
        max=spec.max,
        default=default,
        value=value,
        is_override=service.is_override(guild_id, key),
    )


@router.get("", response_model=list[SettingFieldDTO])
async def list_settings(
    guild_id: int = Depends(require_guild_manager),
    container=Depends(get_container),
) -> list[SettingFieldDTO]:
    service = container.guild_settings
    return [_field(service, guild_id, key) for key in SETTING_SPECS]


@router.put("/{key}", response_model=SettingFieldDTO)
async def update_setting(
    key: str,
    body: SettingUpdate,
    guild_id: int = Depends(require_guild_manager),
    container=Depends(get_container),
) -> SettingFieldDTO:
    if key not in SETTING_SPECS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "неизвестная настройка")
    service = container.guild_settings
    try:
        await service.set(guild_id, key, str(body.value))
    except ValueError as exc:  # невалидное значение или нарушен инвариант
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return _field(service, guild_id, key)


@router.delete("/{key}", response_model=SettingFieldDTO)
async def reset_setting(
    key: str,
    guild_id: int = Depends(require_guild_manager),
    container=Depends(get_container),
) -> SettingFieldDTO:
    if key not in SETTING_SPECS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "неизвестная настройка")
    service = container.guild_settings
    await service.reset(guild_id, key)  # вернётся глобальный дефолт
    return _field(service, guild_id, key)
