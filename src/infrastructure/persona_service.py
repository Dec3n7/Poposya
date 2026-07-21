"""PersonaService — рантайм персон: кэш в памяти + синхронный резолв + CRUD.

Тот же паттерн, что у GuildSettingsService: всё грузится в память на старте
(load_all), чтение синхронное из кэша (годится для горячих путей когов), запись
идёт в БД + pg_notify, а другой процесс (бот ∥ веб-панель) перечитывает кэш по
NOTIFY (PersonaChangeListener). Резолв двухуровневый: override персоны →
дефолт из кода (PHRASE_SPECS / DEFAULT_ATTRIBUTES / файл промпта).

Персон немного (2-4 библиотеки), поэтому reload перечитывает ВСЁ, а не по
одной гильдии — проще и без гонок частичного кэша."""

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.persona.registry import (
    DEFAULT_ATTRIBUTES,
    DEFAULT_MODE,
    DEFAULT_PERSONA_NAME,
    IDENTITY_TEXT_MAX,
    PHRASE_SPECS,
    PRESENCE_LINE_MAX,
    PRESENCE_LINES_MAX,
)
from src.config import Settings
from src.domain.ai_chat.prompt import PromptTemplate
from src.domain.persona.entities import Persona, PersonaPhrase
from src.infrastructure.db.repositories.persona import SqlAlchemyPersonaRepository

logger = logging.getLogger(__name__)

# Канал Postgres LISTEN/NOTIFY: любой писатель (веб-панель) шлёт сюда сигнал,
# бот перечитывает персоны целиком (PersonaChangeListener). Payload не нужен —
# reload перечитывает всё.
PERSONAS_NOTIFY_CHANNEL = "poposya_personas"


def _clean_identity_value(key: str, value: object) -> object:
    """Нормализация и валидация одного атрибута личности. ValueError — наружу
    (роутер отдаст 422). Пустой текст = «вернуть дефолт»."""
    if key in ("display_name", "signature"):
        text_value = str(value or "").strip()
        if len(text_value) > IDENTITY_TEXT_MAX:
            raise ValueError(f"{key}: не длиннее {IDENTITY_TEXT_MAX} символов")
        return text_value or DEFAULT_ATTRIBUTES[key]
    if key == "accent_color":
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFFFF:
            raise ValueError("accent_color: целое 0..0xFFFFFF")
        return value
    if key == "presence":
        if not isinstance(value, list):
            raise ValueError("presence: список строк")
        lines = [str(item).strip() for item in value]
        lines = [line for line in lines if line]
        if len(lines) > PRESENCE_LINES_MAX:
            raise ValueError(f"presence: не больше {PRESENCE_LINES_MAX} строк")
        if any(len(line) > PRESENCE_LINE_MAX for line in lines):
            raise ValueError(f"presence: строка не длиннее {PRESENCE_LINE_MAX} символов")
        return lines
    return value


class PersonaService:
    def __init__(self, settings: Settings, session_factory: async_sessionmaker[AsyncSession]):
        self._settings = settings
        self._session_factory = session_factory
        self._personas: dict[int, Persona] = {}  # id -> Persona
        self._phrases: dict[int, dict[str, PersonaPhrase]] = {}  # persona_id -> key -> phrase
        self._assignments: dict[int, int] = {}  # guild_id -> persona_id
        self._default_id: int | None = None
        # дефолт промпта из файла — fallback, когда Persona.prompt пуст
        self._file_prompt = ""
        self._file_chime_prompt = ""
        # хуки после reload (бот вешает переустановку presence); в API-процессе пусто
        self._reload_hooks: list[Callable[[], Awaitable[None]]] = []

    # --- загрузка / перечитывание ---

    async def load_all(self) -> None:
        self._load_file_defaults()
        async with self._session_factory() as session:
            repo = SqlAlchemyPersonaRepository(session)
            await self._ensure_default(session, repo)
            personas = await repo.list_personas()
            phrases = await repo.list_phrases()
            assignments = await repo.list_assignments()
        self._personas = {p.id: p for p in personas}
        self._phrases = {}
        for phrase in phrases:
            self._phrases.setdefault(phrase.persona_id, {})[phrase.key] = phrase
        self._assignments = assignments
        default = next((p for p in personas if p.is_default), None)
        self._default_id = default.id if default else None
        logger.info(
            "Персоны загружены: %d, назначений %d", len(self._personas), len(self._assignments)
        )

    async def reload(self) -> None:
        """Полное перечитывание по NOTIFY (панель изменила персону). После —
        хуки (напр. переустановка presence); их сбой не роняет reload."""
        await self.load_all()
        for hook in self._reload_hooks:
            try:
                await hook()
            except Exception:
                logger.warning("Хук после reload персон упал", exc_info=True)

    def add_reload_hook(self, hook: Callable[[], Awaitable[None]]) -> None:
        self._reload_hooks.append(hook)

    def _load_file_defaults(self) -> None:
        for attr, path_str in (
            ("_file_prompt", self._settings.ai_prompt_path),
            ("_file_chime_prompt", self._settings.ai_chime_prompt_path),
        ):
            try:
                setattr(self, attr, Path(path_str).read_text(encoding="utf-8"))
            except OSError:
                logger.warning("Не удалось прочитать файл промпта: %s", path_str)

    async def _ensure_default(
        self, session: AsyncSession, repo: SqlAlchemyPersonaRepository
    ) -> None:
        """Гарантирует наличие строки дефолт-персоны.

        В проде миграция 0031 её уже создала — тогда это no-op. В тестах (схема
        через create_all, без миграций) строки нет — создаём здесь. Гонку двух
        писателей на пустой БД гасим откатом: проигравший увидит чужую строку на
        следующем load_all."""
        if await repo.get_default() is not None:
            return
        await repo.create(Persona(id=0, name=DEFAULT_PERSONA_NAME, is_default=True))
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            logger.debug("Дефолт-персона уже создана другим процессом", exc_info=True)

    # --- резолв (синхронно, из кэша) ---

    def _persona_for(self, guild_id: int) -> Persona | None:
        persona_id = self._assignments.get(guild_id, self._default_id)
        if persona_id is None:
            return None
        return self._personas.get(persona_id)

    def for_guild(self, guild_id: int) -> Persona | None:
        return self._persona_for(guild_id)

    def render_prompt(self, guild_id: int, variables: dict[str, object] | None = None) -> str:
        persona = self._persona_for(guild_id)
        template = persona.prompt if persona and persona.prompt else self._file_prompt
        return PromptTemplate(template).render(self._with_identity(guild_id, variables))

    def render_chime_prompt(
        self, guild_id: int, variables: dict[str, object] | None = None
    ) -> str:
        persona = self._persona_for(guild_id)
        template = persona.chime_prompt if persona and persona.chime_prompt else self._file_chime_prompt
        return PromptTemplate(template).render(self._with_identity(guild_id, variables))

    def _with_identity(
        self, guild_id: int, variables: dict[str, object] | None
    ) -> dict[str, object]:
        """{{display_name}}/{{signature}} доступны в любом промпте персоны;
        явные переменные вызова важнее."""
        attrs = self.attributes(guild_id)
        merged: dict[str, object] = {
            "display_name": attrs["display_name"],
            "signature": attrs["signature"],
        }
        if variables:
            merged.update(variables)
        return merged

    def attributes(self, guild_id: int) -> dict[str, object]:
        """Мягкая личность: дефолты из кода, поверх — override персоны."""
        merged = dict(DEFAULT_ATTRIBUTES)
        persona = self._persona_for(guild_id)
        if persona:
            merged.update(persona.attributes)
        return merged

    def accent_color(self, guild_id: int) -> int:
        """Accent эмбедов для сервера; кривой override не роняет ког."""
        value = self.attributes(guild_id).get("accent_color")
        default = DEFAULT_ATTRIBUTES["accent_color"]
        assert isinstance(default, int)
        return value if isinstance(value, int) else default

    def presence_lines(self) -> list[str]:
        """Строки Discord-статуса всех действующих персон (дефолт + назначенные
        серверам), без дублей. Presence у Discord глобальный на бота, поэтому
        собираем общий пул; пусто = встроенный канон PresenceService."""
        ids: list[int] = [] if self._default_id is None else [self._default_id]
        for pid in self._assignments.values():
            if pid not in ids:
                ids.append(pid)
        lines: list[str] = []
        for pid in ids:
            persona = self._personas.get(pid)
            raw = persona.attributes.get("presence") if persona else None
            if not isinstance(raw, list):
                continue
            for item in raw:
                line = str(item).strip()
                if line and line not in lines:
                    lines.append(line)
        return lines

    # --- чтение для API (синхронно, из кэша) ---

    def all_personas(self) -> list[Persona]:
        return list(self._personas.values())

    def get(self, persona_id: int) -> Persona | None:
        return self._personas.get(persona_id)

    def default_id(self) -> int | None:
        return self._default_id

    def assigned_count(self, persona_id: int) -> int:
        """Сколько серверов используют персону (без учёта неявного дефолта)."""
        return sum(1 for pid in self._assignments.values() if pid == persona_id)

    def assigned_persona_id(self, guild_id: int) -> int | None:
        """Персона сервера с учётом дефолта (что реально применяется)."""
        return self._assignments.get(guild_id, self._default_id)

    def file_prompts(self) -> tuple[str, str]:
        """Встроенные дефолты промптов из файлов — для показа в редакторе как
        базовой строки («пусто = вот это»)."""
        return self._file_prompt, self._file_chime_prompt

    def identity_of(self, persona_id: int) -> dict[str, object]:
        """Эффективная личность КОНКРЕТНОЙ персоны (для редактора панели):
        дефолты из кода + override этой персоны (не резолв по гильдии)."""
        persona = self._personas[persona_id]
        return {**DEFAULT_ATTRIBUTES, **persona.attributes}

    def phrase(self, guild_id: int, key: str, **variables: object) -> object:
        """Разрешённое значение фразы: override персоны → дефолт PHRASE_SPECS.

        Для str/template с variables делает .format(**variables). Списки/словари
        возвращаются как есть (выбор элемента/подстановка — на вызывающей стороне
        в P4). KeyError если ключ вне реестра — это ошибка программиста."""
        spec = PHRASE_SPECS[key]
        value: object = spec.default
        persona = self._persona_for(guild_id)
        if persona:
            override = self._phrases.get(persona.id, {}).get(key)
            if override is not None:
                value = override.value
        if variables and isinstance(value, str):
            return value.format(**variables)
        return value

    def phrase_mode(self, guild_id: int, key: str) -> str:
        persona = self._persona_for(guild_id)
        if persona:
            override = self._phrases.get(persona.id, {}).get(key)
            if override is not None:
                return override.mode
        return DEFAULT_MODE

    # --- запись (в БД + pg_notify + локальный reload) ---

    async def create_persona(self, name: str, *, duplicate_of: int | None = None) -> Persona:
        async with self._session_factory() as session:
            repo = SqlAlchemyPersonaRepository(session)
            source = self._personas.get(duplicate_of) if duplicate_of is not None else None
            created = await repo.create(
                Persona(
                    id=0,
                    name=name,
                    is_default=False,
                    prompt=source.prompt if source else "",
                    chime_prompt=source.chime_prompt if source else "",
                    attributes=dict(source.attributes) if source else {},
                )
            )
            if duplicate_of is not None:
                for phrase in self._phrases.get(duplicate_of, {}).values():
                    await repo.set_phrase(
                        PersonaPhrase(created.id, phrase.key, phrase.value, phrase.mode)
                    )
            await self._notify(session)
            await session.commit()
        await self.reload()
        return created

    async def update_persona(self, persona_id: int, **fields: object) -> None:
        allowed = {"name", "prompt", "chime_prompt", "attributes"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"нельзя менять поля: {', '.join(sorted(unknown))}")
        async with self._session_factory() as session:
            repo = SqlAlchemyPersonaRepository(session)
            await repo.update(persona_id, fields)
            await self._notify(session)
            await session.commit()
        await self.reload()

    async def set_identity(self, persona_id: int, identity: dict[str, object]) -> None:
        """Сохранить мягкую личность персоны. В БД остаются только отличия от
        дефолтов (значение, равное дефолту, снимает override) — принцип тот же,
        что у промптов и фраз."""
        persona = self._personas.get(persona_id)
        attrs = dict(persona.attributes) if persona else {}
        for key, value in identity.items():
            if key not in DEFAULT_ATTRIBUTES:
                raise ValueError(f"неизвестный атрибут личности: {key}")
            cleaned = _clean_identity_value(key, value)
            if cleaned == DEFAULT_ATTRIBUTES[key]:
                attrs.pop(key, None)
            else:
                attrs[key] = cleaned
        await self.update_persona(persona_id, attributes=attrs)

    async def delete_persona(self, persona_id: int) -> None:
        persona = self._personas.get(persona_id)
        if persona is not None and persona.is_default:
            raise ValueError("дефолтную персону нельзя удалить")
        async with self._session_factory() as session:
            repo = SqlAlchemyPersonaRepository(session)
            await repo.delete(persona_id)
            await self._notify(session)
            await session.commit()
        await self.reload()

    async def set_phrase(
        self, persona_id: int, key: str, value: object, mode: str = DEFAULT_MODE
    ) -> None:
        spec = PHRASE_SPECS.get(key)
        if spec is None:
            raise ValueError(f"неизвестный ключ фразы: {key}")
        if mode not in spec.allowed_modes:
            raise ValueError(f"режим «{mode}» недопустим для {key}")
        async with self._session_factory() as session:
            repo = SqlAlchemyPersonaRepository(session)
            await repo.set_phrase(PersonaPhrase(persona_id, key, value, mode))
            await self._notify(session)
            await session.commit()
        await self.reload()

    async def reset_phrase(self, persona_id: int, key: str) -> None:
        async with self._session_factory() as session:
            repo = SqlAlchemyPersonaRepository(session)
            await repo.delete_phrase(persona_id, key)
            await self._notify(session)
            await session.commit()
        await self.reload()

    async def assign(self, guild_id: int, persona_id: int) -> None:
        if persona_id not in self._personas:
            raise ValueError(f"персона {persona_id} не существует")
        async with self._session_factory() as session:
            repo = SqlAlchemyPersonaRepository(session)
            await repo.assign(guild_id, persona_id)
            await self._notify(session)
            await session.commit()
        await self.reload()

    # --- перенос библиотеки (замена «версий» и бэкапа) ---

    def export_persona(self, persona_id: int) -> dict:
        """Библиотека как JSON: промпты, атрибуты и все override-фразы. KeyError,
        если персоны нет (роутер отдаёт 404 раньше)."""
        persona = self._personas[persona_id]
        phrases = self._phrases.get(persona_id, {})
        return {
            "name": persona.name,
            "prompt": persona.prompt,
            "chime_prompt": persona.chime_prompt,
            "attributes": persona.attributes,
            "phrases": [
                {"key": p.key, "value": p.value, "mode": p.mode} for p in phrases.values()
            ],
        }

    async def import_persona(self, data: dict) -> Persona:
        """Создать НОВУЮ персону из JSON-выгрузки (never is_default). Незнакомые
        ключи фраз и недопустимые режимы отбрасываются — импорт устойчив к дрейфу
        реестра между версиями."""
        name = str(data.get("name") or "Импортированная персона")
        attributes = data.get("attributes") or {}
        async with self._session_factory() as session:
            repo = SqlAlchemyPersonaRepository(session)
            created = await repo.create(
                Persona(
                    id=0,
                    name=name,
                    is_default=False,
                    prompt=str(data.get("prompt") or ""),
                    chime_prompt=str(data.get("chime_prompt") or ""),
                    attributes=dict(attributes) if isinstance(attributes, dict) else {},
                )
            )
            for raw in data.get("phrases") or []:
                key = raw.get("key")
                spec = PHRASE_SPECS.get(key)
                if spec is None:
                    continue
                mode = raw.get("mode", DEFAULT_MODE)
                if mode not in spec.allowed_modes:
                    mode = DEFAULT_MODE
                await repo.set_phrase(PersonaPhrase(created.id, key, raw.get("value"), mode))
            await self._notify(session)
            await session.commit()
        await self.reload()
        return created

    # --- внутреннее ---

    @staticmethod
    async def _notify(session: AsyncSession) -> None:
        """Транзакционный pg_notify (как в guild_settings): доставится на COMMIT.
        Только Postgres; на SQLite тихо пропускаем (один писатель)."""
        bind = session.bind
        if bind is None or bind.dialect.name != "postgresql":
            return
        await session.execute(
            text("SELECT pg_notify(:channel, :payload)"),
            {"channel": PERSONAS_NOTIFY_CHANNEL, "payload": ""},
        )
