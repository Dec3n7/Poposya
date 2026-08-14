"""Пул лицензионных ключей в панели — ТОЛЬКО оператор бота (require_operator).

Выпуск партий по SKU, просмотр пула и САМИХ ключей (перевыпуск из реестра),
отзыв (soft/hard) и реактивация, экспорт. Серверным админам эти эндпоинты
недоступны — ключи не должны утекать. Аудит keys.* (guild_id=0 — действие
операторское, не привязано к серверу).
"""

from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from src.api.audit import record_audit
from src.api.dependencies import get_container, require_operator
from src.api.security import Session
from src.infrastructure.entitlements import parse_tier
from src.infrastructure.premium_keys.codec import KEY_DURATIONS
from src.infrastructure.premium_keys.service import (
    ActivationView,
    AttemptView,
    BatchView,
    KeyView,
    PremiumKeyService,
    SkuView,
)

router = APIRouter(prefix="/api/admin/premium-keys", tags=["premium-keys"])

_DISABLED = "KEY_SIGNING_SECRET не задан — ключи выключены"


class MintRequest(BaseModel):
    tier: str  # premium | pro
    duration_days: int
    count: int = Field(ge=1, le=10000)
    label: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=256)


class BatchDTO(BaseModel):
    batch_id: int
    label: str
    tier: str
    duration_days: int
    seats: int
    issued: int
    redeemed_seats: int
    capacity: int
    revoked: bool
    created_at: str
    note: str | None


class SkuDTO(BaseModel):
    tier: str
    duration_days: int
    issued: int
    redeemed_seats: int
    capacity: int
    remaining: int


class OverviewDTO(BaseModel):
    enabled: bool
    durations: list[int]
    skus: list[SkuDTO]
    batches: list[BatchDTO]


class KeyDTO(BaseModel):
    key: str
    nonce: str
    seats_used: int
    seats_total: int
    status: str  # unredeemed | partial | full


class MintResponse(BaseModel):
    batch_id: int
    keys: list[str]


class RevokeRequest(BaseModel):
    hard: bool = False
    reason: str = Field(min_length=1, max_length=256)


class RevokeResponse(BaseModel):
    batch_id: int
    hard: bool
    guilds_stripped: int


class ReactivateResponse(BaseModel):
    reactivated: bool


class ActivationDTO(BaseModel):
    nonce: str
    key_masked: str
    guild_id: str  # Discord id строкой — JS теряет точность на >2^53
    user_id: str
    tier: str
    duration_days: int
    batch_id: int
    batch_label: str
    redeemed_at: str


class AttemptDTO(BaseModel):
    user_id: str
    guild_id: str
    at: str
    outcome: str


class ReleaseRequest(BaseModel):
    nonce: str
    guild_id: int


class ReleaseResponse(BaseModel):
    released: bool


def _batch_dto(b: BatchView) -> BatchDTO:
    return BatchDTO(
        batch_id=b.batch_id,
        label=b.label,
        tier=b.tier.name.lower(),
        duration_days=b.duration_days,
        seats=b.seats,
        issued=b.issued,
        redeemed_seats=b.redeemed_seats,
        capacity=b.capacity,
        revoked=b.revoked,
        created_at=b.created_at.replace(tzinfo=UTC).isoformat(),
        note=b.note,
    )


def _sku_dto(s: SkuView) -> SkuDTO:
    return SkuDTO(
        tier=s.tier.name.lower(),
        duration_days=s.duration_days,
        issued=s.issued,
        redeemed_seats=s.redeemed_seats,
        capacity=s.capacity,
        remaining=s.remaining,
    )


def _key_dto(k: KeyView) -> KeyDTO:
    return KeyDTO(
        key=k.key,
        nonce=k.nonce,
        seats_used=k.seats_used,
        seats_total=k.seats_total,
        status=k.status,
    )


def _activation_dto(a: ActivationView) -> ActivationDTO:
    return ActivationDTO(
        nonce=a.nonce,
        key_masked=a.key_masked,
        guild_id=str(a.guild_id),
        user_id=str(a.user_id),
        tier=a.tier.name.lower(),
        duration_days=a.duration_days,
        batch_id=a.batch_id,
        batch_label=a.batch_label,
        redeemed_at=a.redeemed_at.replace(tzinfo=UTC).isoformat(),
    )


def _attempt_dto(a: AttemptView) -> AttemptDTO:
    return AttemptDTO(
        user_id=str(a.user_id),
        guild_id=str(a.guild_id),
        at=a.at.replace(tzinfo=UTC).isoformat(),
        outcome=a.outcome,
    )


def _svc(container) -> PremiumKeyService:
    return container.premium_keys


@router.get("", response_model=OverviewDTO)
async def overview(
    session: Session = Depends(require_operator),
    container=Depends(get_container),
) -> OverviewDTO:
    """Обзор пула: доступные SKU-длительности, инвентарь по SKU и список партий."""
    svc = _svc(container)
    batches = await svc.list_batches()
    skus = await svc.sku_inventory()
    return OverviewDTO(
        enabled=svc.enabled,
        durations=list(KEY_DURATIONS),
        skus=[_sku_dto(s) for s in skus],
        batches=[_batch_dto(b) for b in batches],
    )


@router.post("/batches", response_model=MintResponse)
async def mint_batch(
    body: MintRequest,
    session: Session = Depends(require_operator),
    container=Depends(get_container),
) -> MintResponse:
    """Выпустить партию ключей одного SKU. Возвращает готовые ключи (единственный
    момент, когда они в открытом виде отдаются — дальше перевыпуск из реестра)."""
    svc = _svc(container)
    if not svc.enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _DISABLED)
    try:
        tier = parse_tier(body.tier)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    try:
        batch = await svc.mint_batch(
            tier=tier,
            duration_days=body.duration_days,
            count=body.count,
            label=body.label,
            created_by=session.user_id,
            note=body.note,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    await record_audit(
        container,
        0,
        session.user_id,
        "keys.mint",
        target=f"{tier.name.lower()}-{body.duration_days}",
        details={"batch_id": batch.batch_id, "count": body.count, "label": body.label},
    )
    return MintResponse(batch_id=batch.batch_id, keys=batch.keys)


@router.get("/batches/{batch_id}/keys", response_model=list[KeyDTO])
async def batch_keys(
    batch_id: int,
    session: Session = Depends(require_operator),
    container=Depends(get_container),
) -> list[KeyDTO]:
    """Ключи партии с перевыпуском (панель показывает сами ключи) и статусом."""
    svc = _svc(container)
    if not svc.enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _DISABLED)
    try:
        keys = await svc.batch_keys(batch_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return [_key_dto(k) for k in keys]


@router.post("/batches/{batch_id}/revoke", response_model=RevokeResponse)
async def revoke_batch(
    batch_id: int,
    body: RevokeRequest,
    session: Session = Depends(require_operator),
    container=Depends(get_container),
) -> RevokeResponse:
    """Отозвать партию. soft — блок будущих активаций; hard — плюс снять выданное."""
    svc = _svc(container)
    try:
        result = await svc.revoke_batch(
            batch_id, revoked_by=session.user_id, reason=body.reason, hard=body.hard
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    await record_audit(
        container,
        0,
        session.user_id,
        "keys.revoke",
        target=str(batch_id),
        details={"hard": body.hard, "reason": body.reason, "stripped": result.guilds_stripped},
    )
    return RevokeResponse(
        batch_id=result.batch_id, hard=result.hard, guilds_stripped=result.guilds_stripped
    )


@router.post("/batches/{batch_id}/reactivate", response_model=ReactivateResponse)
async def reactivate_batch(
    batch_id: int,
    session: Session = Depends(require_operator),
    container=Depends(get_container),
) -> ReactivateResponse:
    """Снять soft-отзыв партии (ключи снова активируются)."""
    svc = _svc(container)
    try:
        was = await svc.reactivate_batch(batch_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    await record_audit(container, 0, session.user_id, "keys.reactivate", target=str(batch_id))
    return ReactivateResponse(reactivated=was)


@router.get("/batches/{batch_id}/export", response_class=PlainTextResponse)
async def export_batch(
    batch_id: int,
    only_unredeemed: bool = Query(False),
    session: Session = Depends(require_operator),
    container=Depends(get_container),
) -> str:
    """Плоский список ключей партии (для выгрузки в файл / пул Boosty)."""
    svc = _svc(container)
    if not svc.enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _DISABLED)
    try:
        keys = await svc.export_batch(batch_id, only_unredeemed=only_unredeemed)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return "\n".join(keys)


@router.get("/activations", response_model=list[ActivationDTO])
async def activations(
    guild_id: int | None = Query(None),
    user_id: int | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(require_operator),
    container=Depends(get_container),
) -> list[ActivationDTO]:
    """Журнал активаций: кто/сервер/когда/tier/ключ(маска). Фильтры по серверу и
    пользователю. Источник — потраченные ситы (успешные активации, §7)."""
    rows = await _svc(container).list_activations(
        limit=limit, offset=offset, guild_id=guild_id, user_id=user_id
    )
    return [_activation_dto(a) for a in rows]


@router.get("/attempts", response_model=list[AttemptDTO])
async def attempts(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(require_operator),
    container=Depends(get_container),
) -> list[AttemptDTO]:
    """Лента попыток активации (успех и отказ) — видимость перебора/абуза (§4)."""
    rows = await _svc(container).list_attempts(limit=limit, offset=offset)
    return [_attempt_dto(a) for a in rows]


@router.post("/seats/release", response_model=ReleaseResponse)
async def release_seat(
    body: ReleaseRequest,
    session: Session = Depends(require_operator),
    container=Depends(get_container),
) -> ReleaseResponse:
    """Точечно снять сервер с лицензии (§3): освободить сит (nonce, guild) и снять
    Premium с сервера. Сит возвращается — ключ можно активировать на другом."""
    freed = await _svc(container).release_seat(body.nonce, body.guild_id)
    if not freed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Такого активированного сита нет")
    await record_audit(
        container,
        body.guild_id,
        session.user_id,
        "keys.release_seat",
        target=body.nonce,
    )
    return ReleaseResponse(released=True)
