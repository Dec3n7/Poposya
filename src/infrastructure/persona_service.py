"""PersonaService — рантайм персон: кэш в памяти + синхронный резолв + CRUD.

Тот же паттерн, что у GuildSettingsService: всё грузится в память на старте
(load_all), чтение синхронное из кэша (годится для горячих путей когов), запись
идёт в БД + pg_notify, а другой процесс (бот ∥ веб-панель) перечитывает кэш по
NOTIFY (PersonaChangeListener). Резолв двухуровневый: override персоны →
дефолт из кода (PHRASE_SPECS / DEFAULT_ATTRIBUTES / файл промпта).

Персон немного (2-4 библиотеки), поэтому reload перечитывает ВСЁ, а не по
одной гильдии — проще и без гонок частичного кэша."""

import logging
import random
import string
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypedDict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.persona.registry import (
    DEFAULT_ATTRIBUTES,
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


class ImportReport(TypedDict):
    """Итог импорта персоны: сколько override-фраз принято и что отброшено (с
    причиной) — как по фразам, так и по атрибутам личности."""

    phrases_accepted: int
    phrases_ignored: list[dict[str, object]]
    attributes_ignored: list[dict[str, object]]


def _template_placeholders(text: str) -> set[str]:
    """Имена {плейсхолдеров} в строке (string.Formatter, без позиционных)."""
    try:
        return {field for _, field, _, _ in string.Formatter().parse(text) if field}
    except ValueError:
        return set()


def _format_safe(text: str, variables: dict[str, object]) -> str:
    """format() с защитой: кривой плейсхолдер не роняет ког (текст как есть)."""
    try:
        return text.format(**variables)
    except (KeyError, IndexError, ValueError):
        return text


def _validate_phrase_value(spec, value: object) -> None:
    """Тип по kind + плейсхолдеры-подмножество spec.placeholders. ValueError
    наружу (роутер отдаст 422). Пустые значения допустимы («молчать»)."""
    if spec.kind in ("str", "template"):
        if not isinstance(value, str):
            raise ValueError(f"{spec.key}: ожидается строка")
        texts = [value]
    elif spec.kind == "list":
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ValueError(f"{spec.key}: ожидается список строк")
        texts = value
    elif spec.kind == "dict":
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        ):
            raise ValueError(f"{spec.key}: ожидается словарь строк")
        texts = list(value.values())
    else:
        return
    for item in texts:
        unknown = _template_placeholders(item) - set(spec.placeholders)
        if unknown:
            allowed = ", ".join(sorted(spec.placeholders)) or "нет"
            raise ValueError(
                f"{spec.key}: неизвестные плейсхолдеры {{{', '.join(sorted(unknown))}}} "
                f"(допустимые: {allowed})"
            )


def _replace_in_value(value: object, find: str, replace: str) -> object:
    if isinstance(value, str):
        return value.replace(find, replace)
    if isinstance(value, list):
        return [_replace_in_value(item, find, replace) for item in value]
    if isinstance(value, dict):
        return {k: _replace_in_value(v, find, replace) for k, v in value.items()}
    return value


class PhraseResolver:
    """Резолв фраз каталога и AI-блоков: дефолты из PHRASE_SPECS, override —
    у наследника (_phrase_override). RegistryPersona отдаёт чистые дефолты —
    для когов, собранных без PersonaService (тест-заглушки)."""

    def _phrase_override(self, guild_id: int, key: str) -> PersonaPhrase | None:
        return None

    def phrase(self, guild_id: int, key: str, /, **variables: object) -> object:
        """Разрешённое значение фразы: override персоны → дефолт PHRASE_SPECS.

        guild_id/key — positional-only: иначе плейсхолдер с именем «key»/
        «guild_id» (например у /config) столкнётся с параметром метода.

        Для str/template с variables делает .format(**variables) (лишние
        переменные игнорируются — статика и AI-инструкция блока получают общий
        набор). Списки/словари возвращаются как есть. KeyError если ключ вне
        реестра — это ошибка программиста."""
        spec = PHRASE_SPECS[key]
        value: object = spec.default
        override = self._phrase_override(guild_id, key)
        if override is not None:
            value = override.value
        if variables and isinstance(value, str):
            return _format_safe(value, variables)
        return value

    def phrase_mode(self, guild_id: int, key: str) -> str:
        override = self._phrase_override(guild_id, key)
        if override is not None:
            return override.mode
        return PHRASE_SPECS[key].allowed_modes[0]  # дефолтный режим ключа

    async def render_block(
        self,
        guild_id: int,
        key: str,
        ai_fn: "Callable[[str], Awaitable[str]] | None" = None,
        /,
        **variables: object,
    ) -> str | None:
        """Блок «AI с фолбэком на статику» — общий путь голосовых мест когов.

        mode ключа: ai_then_static — пробуем ai_fn с инструкцией из ключа
        f\"{key}.ai\" (если инструкция или ai_fn отсутствуют/упали — статика);
        static — сразу статика; silent — None (молчим). Статика-список →
        случайный элемент; пустая статика → None (нечего говорить)."""
        mode = self.phrase_mode(guild_id, key)
        if mode == "silent":
            return None
        if mode != "static" and ai_fn is not None and f"{key}.ai" in PHRASE_SPECS:
            instruction = str(self.phrase(guild_id, f"{key}.ai", **variables))
            if instruction:
                try:
                    text = await ai_fn(instruction)
                    if text and text.strip():
                        return text
                except Exception:
                    logger.warning("AI-блок %s не сгенерировался", key, exc_info=True)
        value = self.phrase(guild_id, key, **variables)
        if isinstance(value, list):
            value = random.choice(value) if value else ""
            if variables and isinstance(value, str):
                value = _format_safe(value, variables)
        text = str(value)
        return text or None


class RegistryPersona(PhraseResolver):
    """Только дефолты из кода, без БД и назначений — null-object для когов,
    которым не проброшен PersonaService."""


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


def _accent_to_hex(value: object) -> str:
    """int-цвет -> '#RRGGBB' для человекочитаемой заготовки."""
    if isinstance(value, int) and not isinstance(value, bool):
        return f"#{value & 0xFFFFFF:06X}"
    return "#000000"


def _coerce_accent(value: object) -> object:
    """hex-строка '#RRGGBB' / 'RRGGBB' -> int (заготовку заполняют руками, hex
    привычнее числа). Не-hex возвращаем как есть — валидацию делает
    _clean_identity_value (и отчёт импорта назовёт причину)."""
    if isinstance(value, str):
        s = value.strip().lstrip("#")
        if len(s) == 6:
            try:
                return int(s, 16)
            except ValueError:
                return value
    return value


class PersonaService(PhraseResolver):
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

    def render_chime_prompt(self, guild_id: int, variables: dict[str, object] | None = None) -> str:
        persona = self._persona_for(guild_id)
        template = (
            persona.chime_prompt if persona and persona.chime_prompt else self._file_chime_prompt
        )
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

    def _phrase_override(self, guild_id: int, key: str) -> PersonaPhrase | None:
        """Override фразы персоны сервера (для phrase/phrase_mode/render_block
        из PhraseResolver)."""
        persona = self._persona_for(guild_id)
        if persona is None:
            return None
        return self._phrases.get(persona.id, {}).get(key)

    def phrase_override_of(self, persona_id: int, key: str) -> PersonaPhrase | None:
        """Override КОНКРЕТНОЙ персоны (редактор панели, без резолва по гильдии)."""
        return self._phrases.get(persona_id, {}).get(key)

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
        self, persona_id: int, key: str, value: object, mode: str | None = None
    ) -> None:
        spec = PHRASE_SPECS.get(key)
        if spec is None:
            raise ValueError(f"неизвестный ключ фразы: {key}")
        if mode is None:
            mode = spec.allowed_modes[0]
        if mode not in spec.allowed_modes:
            raise ValueError(f"режим «{mode}» недопустим для {key}")
        _validate_phrase_value(spec, value)
        async with self._session_factory() as session:
            repo = SqlAlchemyPersonaRepository(session)
            await repo.set_phrase(PersonaPhrase(persona_id, key, value, mode))
            await self._notify(session)
            await session.commit()
        await self.reload()

    async def replace_phrases(
        self, persona_id: int, find: str, replace: str, *, dry_run: bool = False
    ) -> list[dict[str, object]]:
        """Find-and-replace по ЭФФЕКТИВНЫМ значениям всех фраз персоны (override
        или дефолт): совпадение в дефолте создаёт override. Возвращает список
        изменений [{key, before, after}]; dry_run — только предпросмотр."""
        if not find:
            raise ValueError("пустая строка поиска")
        changes: list[dict[str, object]] = []
        for key, spec in PHRASE_SPECS.items():
            override = self.phrase_override_of(persona_id, key)
            value = override.value if override is not None else spec.default
            replaced = _replace_in_value(value, find, replace)
            if replaced == value:
                continue
            changes.append({"key": key, "before": value, "after": replaced})
            if not dry_run:
                mode = override.mode if override is not None else None
                await self.set_phrase(persona_id, key, replaced, mode)
        return changes

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
            "phrases": [{"key": p.key, "value": p.value, "mode": p.mode} for p in phrases.values()],
        }

    def build_template(self) -> dict:
        """Пустая заготовка персоны для ручного заполнения (панель отдаёт файлом
        persona-template.json). Ядро сверху пустое, ниже — ВЕСЬ каталог фраз с
        подсказками (_label/_category/_default/_placeholders) и value=null («не
        трогать»). Поля с префиксом _ — только подсказки: import_persona их
        игнорирует (берёт key/value/mode). Дефолты в _default/attributes — из
        того же реестра, что и рантайм, поэтому заготовка не устаревает."""
        attrs = dict(DEFAULT_ATTRIBUTES)
        presence = attrs.get("presence")
        return {
            "_readme": [
                "Заготовка персонажа для бота Попося.",
                "Заполните name и, по желанию, промпты и «Личность». Пустые поля = голос Попоси по умолчанию.",
                "Блок phrases НЕОБЯЗАТЕЛЕН: у каждой строки value=null. Впишите value только тем фразам,"
                " которые хотите переопределить; остальные оставьте null.",
                "Поля с _ в начале (_label, _default, _placeholders …) — подсказки. При импорте они"
                " игнорируются, менять их не нужно.",
                "В шаблонных строках допустимы только вставки из _placeholders (вида {user}); чужие"
                " будут отклонены при импорте с указанием причины.",
                "Готовый файл отдайте оператору — он загрузит его в панели: Персона → Импорт.",
            ],
            "name": "",
            "prompt": "",
            "_prompt": "Системный промпт: кто она, характер, как говорит. Пусто = встроенный характер Попоси.",
            "chime_prompt": "",
            "_chime_prompt": "Когда сама решает вступить в чужой разговор. Пусто = встроенное поведение.",
            "attributes": {
                "display_name": attrs.get("display_name", ""),
                "signature": attrs.get("signature", ""),
                "accent_color": _accent_to_hex(attrs.get("accent_color")),
                "presence": list(presence) if isinstance(presence, list) else [],
            },
            "_attributes": {
                "display_name": "Имя бота в тексте реплик.",
                "signature": "Подпись-эмодзи.",
                "accent_color": "Цвет эмбедов в формате #RRGGBB.",
                "presence": "Строки Discord-статуса (массив); пусто = встроенные занятия Попоси.",
            },
            "phrases": [
                {
                    "key": spec.key,
                    "_label": spec.label,
                    "_category": spec.category,
                    "_kind": spec.kind,
                    "_placeholders": sorted(spec.placeholders),
                    "_modes": list(spec.allowed_modes),
                    "_default": spec.default,
                    "value": None,
                    "mode": spec.allowed_modes[0],
                }
                for spec in PHRASE_SPECS.values()
            ],
        }

    async def import_persona(self, data: dict) -> tuple[Persona, ImportReport]:
        """Создать НОВУЮ персону из JSON (выгрузка или заполненная заготовка;
        never is_default). Возвращает (персона, отчёт): заготовку заполняют
        руками, поэтому молчаливая потеря строки сбивает с толку — отчёт называет,
        что принято и что отброшено с причиной. Поля-подсказки с префиксом _
        игнорируются; фразы с value=null (незаполненные в шаблоне) пропускаются
        без записи override; неизвестные ключи и кривые значения не роняют импорт,
        а попадают в отчёт (устойчивость к дрейфу реестра между версиями)."""
        phrases_ignored: list[dict[str, object]] = []
        attrs_ignored: list[dict[str, object]] = []
        accepted = 0

        name = str(data.get("name") or "Импортированная персона")
        raw_attrs = data.get("attributes")
        attributes: dict[str, object] = {}
        if isinstance(raw_attrs, dict):
            for k, v in raw_attrs.items():
                if k not in DEFAULT_ATTRIBUTES:
                    attrs_ignored.append({"key": str(k), "reason": "неизвестный атрибут"})
                    continue
                value = _coerce_accent(v) if k == "accent_color" else v
                try:
                    cleaned = _clean_identity_value(k, value)
                except ValueError as exc:
                    attrs_ignored.append({"key": str(k), "reason": str(exc)})
                    continue
                if cleaned != DEFAULT_ATTRIBUTES[k]:  # в БД только отличия от дефолта
                    attributes[k] = cleaned

        async with self._session_factory() as session:
            repo = SqlAlchemyPersonaRepository(session)
            created = await repo.create(
                Persona(
                    id=0,
                    name=name,
                    is_default=False,
                    prompt=str(data.get("prompt") or ""),
                    chime_prompt=str(data.get("chime_prompt") or ""),
                    attributes=attributes,
                )
            )
            raw_phrases = data.get("phrases")
            for raw in raw_phrases if isinstance(raw_phrases, list) else []:
                if not isinstance(raw, dict):
                    phrases_ignored.append({"key": None, "reason": "строка не объект"})
                    continue
                if raw.get("value") is None:  # незаполненная строка шаблона — норма
                    continue
                key = raw.get("key")
                spec = PHRASE_SPECS.get(key) if isinstance(key, str) else None
                if spec is None:
                    phrases_ignored.append(
                        {"key": key if isinstance(key, str) else None, "reason": "неизвестный ключ"}
                    )
                    continue
                value = raw.get("value")
                try:
                    _validate_phrase_value(spec, value)
                except ValueError as exc:
                    phrases_ignored.append({"key": spec.key, "reason": str(exc)})
                    continue
                mode = raw.get("mode", spec.allowed_modes[0])
                if mode not in spec.allowed_modes:
                    mode = spec.allowed_modes[0]
                await repo.set_phrase(PersonaPhrase(created.id, spec.key, value, mode))
                accepted += 1
            await self._notify(session)
            await session.commit()
        await self.reload()
        return created, {
            "phrases_accepted": accepted,
            "phrases_ignored": phrases_ignored,
            "attributes_ignored": attrs_ignored,
        }

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
