"""Пер-серверные настройки в вебе — тонкая обёртка над `GuildSettingsService`.

Никакой своей бизнес-логики: валидация (поле + кросс-поле), сохранение и рассылка
`pg_notify` (бот подхватит без рестарта) уже внутри сервиса — того же, что и у
`/config` в Discord. Список редактируемых полей и их типы — из `SETTING_SPECS`.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status

from src.api.audit import record_audit
from src.api.dependencies import current_session, get_container, require_guild_manager
from src.api.schemas import BatchUpdate, SettingFieldDTO, SettingUpdate
from src.api.security import Session
from src.application.guild_config.schema import SETTING_KEYS
from src.infrastructure.guild_settings import (
    FEATURE_FLAG_KEYS,
    FEATURE_MODULES,
    SETTING_SPECS,
    GuildSettingsService,
)

router = APIRouter(prefix="/api/guilds/{guild_id}/settings", tags=["settings"])

# списочные/словарные ключи (не в SETTING_SPECS — там только скаляры)
_COMPLEX_LABELS = {
    "relationship_role_thresholds": "Пороги очков для ролей",
    "relationship_role_names": "Имена ролей-статусов",
    "ai_rate_limits_by_level": "Лимиты AI-реплик в час по уровню",
}


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
    # тумблеры модулей здесь не показываем — у них своя вкладка «Модули»
    return [_field(service, guild_id, key) for key in SETTING_SPECS if key not in FEATURE_FLAG_KEYS]


@router.get("/modules")
async def list_modules(
    guild_id: int = Depends(require_guild_manager),
    container=Depends(get_container),
) -> list[dict]:
    """Отключаемые модули и их подфункции (вкладка «Модули»). Значения — из тех же
    пер-серверных настроек; запись идёт через PUT /settings/{key} по каждому флагу."""
    s: GuildSettingsService = container.guild_settings

    def flag(key: str) -> dict:
        return {
            "key": key,
            "label": SETTING_SPECS[key].label,
            "value": bool(s.current(guild_id, key)),
            "is_override": s.is_override(guild_id, key),
        }

    return [
        {
            "key": m.key,
            "label": m.label,
            "description": m.description,
            "master": flag(m.master),
            "subs": [flag(k) for k in m.subs],
        }
        for m in FEATURE_MODULES
    ]


@router.get("/complex")
async def complex_settings(
    guild_id: int = Depends(require_guild_manager),
    container=Depends(get_container),
) -> dict[str, object]:
    """Списочные/словарные настройки (роли, лимиты) — свой формат: значение и
    дефолт как есть (список/словарь). Роли отдаём группой: пороги и имена
    редактируются вместе (кросс-инвариант len(имён) == len(порогов)+1)."""
    s: GuildSettingsService = container.guild_settings

    def field(key: str) -> dict[str, object]:
        return {
            "value": s.current(guild_id, key),
            "default": s.default(key),
            "is_override": s.is_override(guild_id, key),
        }

    return {
        "role_thresholds": {
            "label": _COMPLEX_LABELS["relationship_role_thresholds"],
            **field("relationship_role_thresholds"),
        },
        "role_names": {
            "label": _COMPLEX_LABELS["relationship_role_names"],
            **field("relationship_role_names"),
        },
        "rate_limits": {
            "label": _COMPLEX_LABELS["ai_rate_limits_by_level"],
            **field("ai_rate_limits_by_level"),
        },
    }


@router.put("/batch", status_code=status.HTTP_204_NO_CONTENT)
async def update_batch(
    body: BatchUpdate,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container=Depends(get_container),
) -> Response:
    """Несколько настроек разом (роли: пороги+имена; лимиты). Валидируются
    вместе через set_many — связанные ключи меняются согласованно."""
    unknown = [k for k in body.values if k not in SETTING_KEYS]
    if unknown:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"неизвестные ключи: {', '.join(unknown)}")
    try:
        await container.guild_settings.set_many(guild_id, body.values)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "settings.batch",
        details={"keys": list(body.values.keys())},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{key}", response_model=SettingFieldDTO)
async def update_setting(
    key: str,
    body: SettingUpdate,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container=Depends(get_container),
) -> SettingFieldDTO:
    if key not in SETTING_SPECS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "неизвестная настройка")
    service = container.guild_settings
    try:
        await service.set(guild_id, key, str(body.value))
    except ValueError as exc:  # невалидное значение или нарушен инвариант
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "settings.set",
        target=key,
        details={"value": body.value},
    )
    return _field(service, guild_id, key)


@router.delete("/{key}", response_model=SettingFieldDTO)
async def reset_setting(
    key: str,
    guild_id: int = Depends(require_guild_manager),
    session: Session = Depends(current_session),
    container=Depends(get_container),
) -> SettingFieldDTO:
    if key not in SETTING_SPECS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "неизвестная настройка")
    service = container.guild_settings
    await service.reset(guild_id, key)  # вернётся глобальный дефолт
    await record_audit(container, guild_id, session.user_id, "settings.reset", target=key)
    return _field(service, guild_id, key)
