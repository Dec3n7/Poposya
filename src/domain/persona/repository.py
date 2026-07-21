from abc import ABC, abstractmethod

from src.domain.persona.entities import Persona, PersonaPhrase


class IPersonaRepository(ABC):
    """Хранение персон, их override-фраз и назначений серверам. Только storage —
    без бизнес-логики (резолв и валидация — в PersonaService)."""

    # --- чтение (наполнение кэша сервиса) ---

    @abstractmethod
    async def list_personas(self) -> list[Persona]: ...

    @abstractmethod
    async def list_phrases(self) -> list[PersonaPhrase]: ...

    @abstractmethod
    async def list_assignments(self) -> dict[int, int]:
        """guild_id -> persona_id."""
        ...

    @abstractmethod
    async def get_default(self) -> Persona | None: ...

    # --- запись ---

    @abstractmethod
    async def create(self, persona: Persona) -> Persona:
        """Вставить персону (id игнорируется); вернуть с присвоенным id."""
        ...

    @abstractmethod
    async def update(self, persona_id: int, fields: dict[str, object]) -> None: ...

    @abstractmethod
    async def delete(self, persona_id: int) -> None:
        """Удалить персону вместе с её override-фразами (каскад в репозитории —
        FK в проекте не используется)."""
        ...

    @abstractmethod
    async def set_phrase(self, phrase: PersonaPhrase) -> None:
        """Upsert override-фразы по (persona_id, key)."""
        ...

    @abstractmethod
    async def delete_phrase(self, persona_id: int, key: str) -> None: ...

    @abstractmethod
    async def assign(self, guild_id: int, persona_id: int) -> None:
        """Upsert назначения персоны серверу."""
        ...
