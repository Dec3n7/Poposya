import json
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.persona.entities import Persona, PersonaPhrase
from src.domain.persona.repository import IPersonaRepository
from src.infrastructure.db.models.persona import (
    GuildPersonaModel,
    PersonaModel,
    PersonaPhraseModel,
)


def _upsert(session: AsyncSession):
    name = session.bind.dialect.name if session.bind is not None else "sqlite"
    return pg_insert if name == "postgresql" else sqlite_insert


def _to_persona(row: PersonaModel) -> Persona:
    return Persona(
        id=row.id,
        name=row.name,
        is_default=row.is_default,
        prompt=row.prompt,
        chime_prompt=row.chime_prompt,
        attributes=json.loads(row.attributes) if row.attributes else {},
        created_at=row.created_at.replace(tzinfo=UTC) if row.created_at else None,
        updated_at=row.updated_at.replace(tzinfo=UTC) if row.updated_at else None,
    )


class SqlAlchemyPersonaRepository(IPersonaRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_personas(self) -> list[Persona]:
        rows = (await self._session.execute(select(PersonaModel))).scalars().all()
        return [_to_persona(r) for r in rows]

    async def list_phrases(self) -> list[PersonaPhrase]:
        rows = (await self._session.execute(select(PersonaPhraseModel))).scalars().all()
        return [
            PersonaPhrase(
                persona_id=r.persona_id,
                key=r.key,
                value=json.loads(r.value),
                mode=r.mode,
            )
            for r in rows
        ]

    async def list_assignments(self) -> dict[int, int]:
        rows = (await self._session.execute(select(GuildPersonaModel))).scalars().all()
        return {r.guild_id: r.persona_id for r in rows}

    async def get_default(self) -> Persona | None:
        row = (
            await self._session.execute(
                select(PersonaModel).where(PersonaModel.is_default.is_(True))
            )
        ).scalars().first()
        return _to_persona(row) if row else None

    async def create(self, persona: Persona) -> Persona:
        now = datetime.now(UTC).replace(tzinfo=None)
        model = PersonaModel(
            name=persona.name,
            is_default=persona.is_default,
            prompt=persona.prompt,
            chime_prompt=persona.chime_prompt,
            attributes=json.dumps(persona.attributes, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        )
        self._session.add(model)
        await self._session.flush()  # получить autoincrement id
        return _to_persona(model)

    async def update(self, persona_id: int, fields: dict[str, object]) -> None:
        row = await self._session.get(PersonaModel, persona_id)
        if row is None:
            return
        if "name" in fields:
            row.name = str(fields["name"])
        if "prompt" in fields:
            row.prompt = str(fields["prompt"])
        if "chime_prompt" in fields:
            row.chime_prompt = str(fields["chime_prompt"])
        if "attributes" in fields:
            row.attributes = json.dumps(fields["attributes"], ensure_ascii=False)
        row.updated_at = datetime.now(UTC).replace(tzinfo=None)

    async def delete(self, persona_id: int) -> None:
        # каскад фраз вручную — FK в проекте не используется
        await self._session.execute(
            delete(PersonaPhraseModel).where(PersonaPhraseModel.persona_id == persona_id)
        )
        await self._session.execute(delete(PersonaModel).where(PersonaModel.id == persona_id))
        await self._session.execute(
            delete(GuildPersonaModel).where(GuildPersonaModel.persona_id == persona_id)
        )

    async def set_phrase(self, phrase: PersonaPhrase) -> None:
        values = {
            "persona_id": phrase.persona_id,
            "key": phrase.key,
            "value": json.dumps(phrase.value, ensure_ascii=False),
            "mode": phrase.mode,
        }
        stmt = _upsert(self._session)(PersonaPhraseModel).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["persona_id", "key"],
            set_={"value": values["value"], "mode": values["mode"]},
        )
        await self._session.execute(stmt)

    async def delete_phrase(self, persona_id: int, key: str) -> None:
        await self._session.execute(
            delete(PersonaPhraseModel).where(
                PersonaPhraseModel.persona_id == persona_id,
                PersonaPhraseModel.key == key,
            )
        )

    async def assign(self, guild_id: int, persona_id: int) -> None:
        values = {"guild_id": guild_id, "persona_id": persona_id}
        stmt = _upsert(self._session)(GuildPersonaModel).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["guild_id"],
            set_={"persona_id": persona_id},
        )
        await self._session.execute(stmt)
