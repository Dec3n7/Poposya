"""Тарифы серверов (подписки) в панели — выдаёт ТОЛЬКО оператор бота.

Тонкая обёртка над EntitlementService: запись идёт в БД + pg_notify (бот и
второй инстанс перечитывают тариф без рестарта — как с настройками/персонами).
Под require_operator: серверные админы сюда не ходят (иначе любой админ выдал бы
себе Premium). Аудит entitlement.*.

Раскатка: пока ENTITLEMENTS_DEFAULT_TIER=pro, enforcement выключен и выдача ничего
не «включает» визуально (все и так PRO). Смысл появляется, когда оператор ставит
default_tier=free — тогда подписки начинают отличать сервер от базового тарифа."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.audit import record_audit
from src.api.dependencies import get_container, require_operator
from src.api.schemas import EntitlementDTO, EntitlementGrant
from src.api.security import Session
from src.application.interfaces.entitlements import PlanTier
from src.infrastructure.entitlements import EntitlementService, parse_tier

router = APIRouter(prefix="/api/guilds/{guild_id}/entitlement", tags=["entitlements"])


def _dto(service: EntitlementService, guild_id: int) -> EntitlementDTO:
    tier, expires_at, active = service.current(guild_id)
    return EntitlementDTO(
        guild_id=str(guild_id),
        tier=tier.name.lower(),
        active=active,
        # наивный UTC из БД -> помечаем зоной, чтобы JS распарсил как UTC
        expires_at=(expires_at.replace(tzinfo=UTC).isoformat() if expires_at else None),
        default_tier=service.default_tier.name.lower(),
        enforced=service.default_tier is not PlanTier.PRO,
    )


@router.get("", response_model=EntitlementDTO)
async def get_entitlement(
    guild_id: int,
    session: Session = Depends(require_operator),
    container=Depends(get_container),
) -> EntitlementDTO:
    return _dto(container.entitlements, guild_id)


@router.put("", response_model=EntitlementDTO)
async def grant_entitlement(
    guild_id: int,
    body: EntitlementGrant,
    session: Session = Depends(require_operator),
    container=Depends(get_container),
) -> EntitlementDTO:
    try:
        tier = parse_tier(body.tier)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    expires_at = None
    if body.duration_days:  # None или 0 -> бессрочно
        expires_at = datetime.now(UTC) + timedelta(days=body.duration_days)
    await container.entitlements.grant(guild_id, tier, expires_at, session.user_id)
    await record_audit(
        container,
        guild_id,
        session.user_id,
        "entitlement.grant",
        target=tier.name.lower(),
        details={"tier": tier.name.lower(), "duration_days": body.duration_days},
    )
    return _dto(container.entitlements, guild_id)


@router.delete("", response_model=EntitlementDTO)
async def revoke_entitlement(
    guild_id: int,
    session: Session = Depends(require_operator),
    container=Depends(get_container),
) -> EntitlementDTO:
    existed = await container.entitlements.revoke(guild_id)
    if existed:
        await record_audit(container, guild_id, session.user_id, "entitlement.revoke")
    return _dto(container.entitlements, guild_id)
