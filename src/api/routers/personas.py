"""Библиотеки персон — управление текстом/личностью бота (ТОЛЬКО оператор).

Тонкая обёртка над PersonaService: вся запись идёт в БД + pg_notify (бот и
второй инстанс перечитывают персону без рестарта — как с настройками). Роуты под
require_operator: серверные админы сюда не ходят. Аудит persona.* (guild_id=0 для
не-гильдийных операций, реальный guild — при назначении сервера)."""

from fastapi import APIRouter, Depends, HTTPException, Response, status

from src.api.audit import record_audit
from src.api.dependencies import get_container, require_operator
from src.api.schemas import (
    GuildPersonaAssign,
    GuildPersonaDTO,
    PersonaCreate,
    PersonaDetailDTO,
    PersonaIdentityDTO,
    PersonaIdentityUpdate,
    PersonaPhraseDTO,
    PersonaRename,
    PersonaSummaryDTO,
    PhraseChangeDTO,
    PhraseReplace,
    PhraseUpdate,
    PromptUpdate,
)
from src.api.security import Session
from src.application.persona.registry import DEFAULT_ATTRIBUTES, PHRASE_SPECS
from src.domain.persona.entities import Persona
from src.infrastructure.persona_service import PersonaService

router = APIRouter(prefix="/api", tags=["personas"])

# guild_id-заглушка для аудита операций, не привязанных к конкретному серверу
_GLOBAL = 0


def _summary(service: PersonaService, persona: Persona) -> PersonaSummaryDTO:
    return PersonaSummaryDTO(
        id=persona.id,
        name=persona.name,
        is_default=persona.is_default,
        assigned_count=service.assigned_count(persona.id),
    )


def _detail(service: PersonaService, persona: Persona) -> PersonaDetailDTO:
    file_prompt, file_chime = service.file_prompts()
    return PersonaDetailDTO(
        id=persona.id,
        name=persona.name,
        is_default=persona.is_default,
        prompt=persona.prompt,
        chime_prompt=persona.chime_prompt,
        default_prompt=file_prompt,
        default_chime_prompt=file_chime,
        assigned_count=service.assigned_count(persona.id),
    )


def _require(service: PersonaService, persona_id: int) -> Persona:
    persona = service.get(persona_id)
    if persona is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "персона не найдена")
    return persona


@router.get("/personas", response_model=list[PersonaSummaryDTO])
async def list_personas(
    _op: Session = Depends(require_operator), container=Depends(get_container)
) -> list[PersonaSummaryDTO]:
    service: PersonaService = container.persona
    return [_summary(service, p) for p in service.all_personas()]


@router.post("/personas", response_model=PersonaDetailDTO, status_code=status.HTTP_201_CREATED)
async def create_persona(
    body: PersonaCreate,
    op: Session = Depends(require_operator),
    container=Depends(get_container),
) -> PersonaDetailDTO:
    service: PersonaService = container.persona
    if body.duplicate_of is not None:
        _require(service, body.duplicate_of)
    try:
        created = await service.create_persona(body.name, duplicate_of=body.duplicate_of)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    await record_audit(
        container, _GLOBAL, op.user_id, "persona.create",
        target=created.id, details={"name": body.name, "duplicate_of": body.duplicate_of},
    )
    return _detail(service, _require(service, created.id))


@router.post(
    "/personas/{persona_id}/duplicate",
    response_model=PersonaDetailDTO,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_persona(
    persona_id: int,
    body: PersonaRename,
    op: Session = Depends(require_operator),
    container=Depends(get_container),
) -> PersonaDetailDTO:
    service: PersonaService = container.persona
    _require(service, persona_id)
    created = await service.create_persona(body.name, duplicate_of=persona_id)
    await record_audit(
        container, _GLOBAL, op.user_id, "persona.duplicate",
        target=created.id, details={"from": persona_id},
    )
    return _detail(service, _require(service, created.id))


@router.post("/personas/import", response_model=PersonaDetailDTO, status_code=status.HTTP_201_CREATED)
async def import_persona(
    body: dict,
    op: Session = Depends(require_operator),
    container=Depends(get_container),
) -> PersonaDetailDTO:
    service: PersonaService = container.persona
    created = await service.import_persona(body)
    await record_audit(
        container, _GLOBAL, op.user_id, "persona.import",
        target=created.id, details={"name": created.name},
    )
    return _detail(service, _require(service, created.id))


@router.get("/personas/{persona_id}", response_model=PersonaDetailDTO)
async def get_persona(
    persona_id: int,
    _op: Session = Depends(require_operator),
    container=Depends(get_container),
) -> PersonaDetailDTO:
    service: PersonaService = container.persona
    return _detail(service, _require(service, persona_id))


@router.patch("/personas/{persona_id}", response_model=PersonaDetailDTO)
async def rename_persona(
    persona_id: int,
    body: PersonaRename,
    op: Session = Depends(require_operator),
    container=Depends(get_container),
) -> PersonaDetailDTO:
    service: PersonaService = container.persona
    _require(service, persona_id)
    await service.update_persona(persona_id, name=body.name)
    await record_audit(
        container, _GLOBAL, op.user_id, "persona.rename", target=persona_id,
        details={"name": body.name},
    )
    return _detail(service, _require(service, persona_id))


@router.delete("/personas/{persona_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_persona(
    persona_id: int,
    op: Session = Depends(require_operator),
    container=Depends(get_container),
) -> Response:
    service: PersonaService = container.persona
    _require(service, persona_id)
    try:
        await service.delete_persona(persona_id)
    except ValueError as exc:  # дефолтную персону удалять нельзя
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    await record_audit(container, _GLOBAL, op.user_id, "persona.delete", target=persona_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/personas/{persona_id}/prompt", response_model=PersonaDetailDTO)
async def set_prompt(
    persona_id: int,
    body: PromptUpdate,
    op: Session = Depends(require_operator),
    container=Depends(get_container),
) -> PersonaDetailDTO:
    service: PersonaService = container.persona
    _require(service, persona_id)
    await service.update_persona(persona_id, prompt=body.prompt)
    await record_audit(
        container, _GLOBAL, op.user_id, "persona.prompt", target=persona_id,
        details={"reset": body.prompt == ""},
    )
    return _detail(service, _require(service, persona_id))


@router.put("/personas/{persona_id}/chime_prompt", response_model=PersonaDetailDTO)
async def set_chime_prompt(
    persona_id: int,
    body: PromptUpdate,
    op: Session = Depends(require_operator),
    container=Depends(get_container),
) -> PersonaDetailDTO:
    service: PersonaService = container.persona
    _require(service, persona_id)
    await service.update_persona(persona_id, chime_prompt=body.prompt)
    await record_audit(
        container, _GLOBAL, op.user_id, "persona.chime_prompt", target=persona_id,
        details={"reset": body.prompt == ""},
    )
    return _detail(service, _require(service, persona_id))


def _as_int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _identity_dto(service: PersonaService, persona_id: int) -> PersonaIdentityDTO:
    identity = service.identity_of(persona_id)
    presence = identity.get("presence")
    return PersonaIdentityDTO(
        display_name=str(identity.get("display_name", "")),
        signature=str(identity.get("signature", "")),
        accent_color=_as_int(identity.get("accent_color")),
        presence=[str(line) for line in presence] if isinstance(presence, list) else [],
        default_display_name=str(DEFAULT_ATTRIBUTES["display_name"]),
        default_signature=str(DEFAULT_ATTRIBUTES["signature"]),
        default_accent_color=_as_int(DEFAULT_ATTRIBUTES["accent_color"]),
    )


@router.get("/personas/{persona_id}/identity", response_model=PersonaIdentityDTO)
async def get_identity(
    persona_id: int,
    _op: Session = Depends(require_operator),
    container=Depends(get_container),
) -> PersonaIdentityDTO:
    service: PersonaService = container.persona
    _require(service, persona_id)
    return _identity_dto(service, persona_id)


@router.put("/personas/{persona_id}/identity", response_model=PersonaIdentityDTO)
async def set_identity(
    persona_id: int,
    body: PersonaIdentityUpdate,
    op: Session = Depends(require_operator),
    container=Depends(get_container),
) -> PersonaIdentityDTO:
    service: PersonaService = container.persona
    _require(service, persona_id)
    try:
        await service.set_identity(persona_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    await record_audit(
        container, _GLOBAL, op.user_id, "persona.identity", target=persona_id,
        details={"presence_lines": len(body.presence)},
    )
    return _identity_dto(service, persona_id)


def _phrase_dto(service: PersonaService, persona_id: int, key: str) -> PersonaPhraseDTO:
    spec = PHRASE_SPECS[key]
    override = service.phrase_override_of(persona_id, key)
    return PersonaPhraseDTO(
        key=spec.key,
        label=spec.label,
        category=spec.category,
        kind=spec.kind,
        default=spec.default,
        value=override.value if override is not None else None,
        mode=override.mode if override is not None else spec.allowed_modes[0],
        is_override=override is not None,
        placeholders=sorted(spec.placeholders),
        allowed_modes=list(spec.allowed_modes),
    )


@router.get("/personas/{persona_id}/phrases", response_model=list[PersonaPhraseDTO])
async def list_phrases(
    persona_id: int,
    _op: Session = Depends(require_operator),
    container=Depends(get_container),
) -> list[PersonaPhraseDTO]:
    """Весь каталог фраз персоны в порядке реестра (фронт группирует по
    category)."""
    service: PersonaService = container.persona
    _require(service, persona_id)
    return [_phrase_dto(service, persona_id, key) for key in PHRASE_SPECS]


@router.post("/personas/{persona_id}/phrases/replace", response_model=list[PhraseChangeDTO])
async def replace_phrases(
    persona_id: int,
    body: PhraseReplace,
    op: Session = Depends(require_operator),
    container=Depends(get_container),
) -> list[PhraseChangeDTO]:
    service: PersonaService = container.persona
    _require(service, persona_id)
    try:
        changes = await service.replace_phrases(
            persona_id, body.find, body.replace, dry_run=body.dry_run
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    if not body.dry_run and changes:
        await record_audit(
            container, _GLOBAL, op.user_id, "persona.replace", target=persona_id,
            details={"find": body.find, "replace": body.replace, "keys": len(changes)},
        )
    return [PhraseChangeDTO(**change) for change in changes]


@router.put("/personas/{persona_id}/phrases/{key}", response_model=PersonaPhraseDTO)
async def set_phrase(
    persona_id: int,
    key: str,
    body: PhraseUpdate,
    op: Session = Depends(require_operator),
    container=Depends(get_container),
) -> PersonaPhraseDTO:
    service: PersonaService = container.persona
    _require(service, persona_id)
    if key not in PHRASE_SPECS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "нет такого ключа фразы")
    try:
        await service.set_phrase(persona_id, key, body.value, body.mode)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    await record_audit(
        container, _GLOBAL, op.user_id, "persona.phrase", target=persona_id,
        details={"key": key},
    )
    return _phrase_dto(service, persona_id, key)


@router.delete("/personas/{persona_id}/phrases/{key}", response_model=PersonaPhraseDTO)
async def reset_phrase(
    persona_id: int,
    key: str,
    op: Session = Depends(require_operator),
    container=Depends(get_container),
) -> PersonaPhraseDTO:
    service: PersonaService = container.persona
    _require(service, persona_id)
    if key not in PHRASE_SPECS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "нет такого ключа фразы")
    await service.reset_phrase(persona_id, key)
    await record_audit(
        container, _GLOBAL, op.user_id, "persona.phrase_reset", target=persona_id,
        details={"key": key},
    )
    return _phrase_dto(service, persona_id, key)


@router.get("/personas/{persona_id}/export")
async def export_persona(
    persona_id: int,
    _op: Session = Depends(require_operator),
    container=Depends(get_container),
) -> dict:
    service: PersonaService = container.persona
    _require(service, persona_id)
    return service.export_persona(persona_id)


@router.get("/guilds/{guild_id}/persona", response_model=GuildPersonaDTO)
async def get_guild_persona(
    guild_id: int,
    _op: Session = Depends(require_operator),
    container=Depends(get_container),
) -> GuildPersonaDTO:
    service: PersonaService = container.persona
    return GuildPersonaDTO(
        guild_id=str(guild_id), persona_id=service.assigned_persona_id(guild_id) or 0
    )


@router.put("/guilds/{guild_id}/persona", response_model=GuildPersonaDTO)
async def assign_guild_persona(
    guild_id: int,
    body: GuildPersonaAssign,
    op: Session = Depends(require_operator),
    container=Depends(get_container),
) -> GuildPersonaDTO:
    service: PersonaService = container.persona
    try:
        await service.assign(guild_id, body.persona_id)
    except ValueError as exc:  # персоны не существует
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    await record_audit(
        container, guild_id, op.user_id, "persona.assign", target=body.persona_id
    )
    return GuildPersonaDTO(guild_id=str(guild_id), persona_id=body.persona_id)
