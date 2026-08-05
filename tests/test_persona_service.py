"""PersonaService: резолв (2 уровня), CRUD, дублирование, назначение, режимы.

Схема поднимается через create_all (conftest), без миграций — значит проверяем
и то, что load_all идемпотентно создаёт дефолт-персону (_ensure_default)."""

import pytest

from src.application.persona.registry import PHRASE_SPECS
from src.config import Settings
from src.infrastructure.persona_service import PersonaService


def make_settings(**over):
    return Settings(_env_file=None, discord_token="t", **over)


@pytest.fixture
async def svc(session_factory):
    service = PersonaService(make_settings(), session_factory)
    await service.load_all()
    return service


async def test_ensure_default_creates_row(svc):
    # незаассайненная гильдия резолвится в дефолт-персону, созданную load_all
    persona = svc.for_guild(123)
    assert persona is not None
    assert persona.is_default
    assert persona.name == "Попося"


async def test_ensure_default_is_idempotent(session_factory):
    service = PersonaService(make_settings(), session_factory)
    await service.load_all()
    await service.load_all()  # второй раз не должен плодить дефолт-строки
    defaults = [p for p in service._personas.values() if p.is_default]
    assert len(defaults) == 1


async def test_phrase_falls_back_to_registry_default(svc):
    assert svc.phrase(123, "activity.farewell", name="Аня") == "Аня ушёл. Бывает."


async def test_phrase_list_default_returned_as_is(svc):
    assert svc.phrase(123, "ai_chat.error_replies") == PHRASE_SPECS["ai_chat.error_replies"].default


async def test_override_wins_and_is_per_guild(svc):
    persona = await svc.create_persona("Резкий")
    await svc.set_phrase(persona.id, "activity.farewell", "{name} свалил. Скатертью дорога.")
    await svc.assign(999, persona.id)
    # заассайненная гильдия видит override
    assert svc.phrase(999, "activity.farewell", name="Игорь") == "Игорь свалил. Скатертью дорога."
    # другая гильдия — по-прежнему дефолт из реестра
    assert svc.phrase(1, "activity.farewell", name="Игорь") == "Игорь ушёл. Бывает."


async def test_duplicate_copies_phrases(svc):
    base = await svc.create_persona("База")
    await svc.set_phrase(base.id, "activity.welcome", "Привет-привет, {name}!")
    dup = await svc.create_persona("Копия", duplicate_of=base.id)
    await svc.assign(555, dup.id)
    assert svc.phrase(555, "activity.welcome", name="Кот") == "Привет-привет, Кот!"


async def test_reset_phrase_returns_to_default(svc):
    persona = await svc.create_persona("Врем")
    await svc.assign(42, persona.id)
    await svc.set_phrase(persona.id, "activity.farewell", "custom {name}")
    assert svc.phrase(42, "activity.farewell", name="X") == "custom X"
    await svc.reset_phrase(persona.id, "activity.farewell")
    assert svc.phrase(42, "activity.farewell", name="X") == "X ушёл. Бывает."


async def test_cannot_delete_default(svc):
    default_id = svc.for_guild(1).id
    with pytest.raises(ValueError):
        await svc.delete_persona(default_id)


async def test_set_phrase_rejects_unknown_key(svc):
    persona = await svc.create_persona("X")
    with pytest.raises(ValueError):
        await svc.set_phrase(persona.id, "nope.key", "x")


async def test_set_phrase_rejects_bad_mode(svc):
    persona = await svc.create_persona("X")
    with pytest.raises(ValueError):
        await svc.set_phrase(persona.id, "activity.welcome", "hi {name}", mode="whatever")


async def test_assign_rejects_missing_persona(svc):
    with pytest.raises(ValueError):
        await svc.assign(1, 999999)


async def test_render_prompt_uses_persona_override(svc):
    persona = await svc.create_persona("Промпт-персона")
    await svc.update_persona(persona.id, prompt="Ты — {{name}}. Отвечай кратко.")
    await svc.assign(777, persona.id)
    assert svc.render_prompt(777, {"name": "Тест"}) == "Ты — Тест. Отвечай кратко."


async def test_render_prompt_default_does_not_crash(svc):
    # дефолт-персона с пустым prompt → фолбэк на файл (или '' если файла нет)
    assert isinstance(svc.render_prompt(1), str)


async def test_attributes_default_resolution(svc):
    attrs = svc.attributes(1)
    assert attrs["display_name"] == "Попося"
    assert attrs["accent_color"] == 0x9B59B6


async def test_attributes_override_merges(svc):
    persona = await svc.create_persona("Мужской голос")
    await svc.update_persona(persona.id, attributes={"display_name": "Попыч"})
    await svc.assign(88, persona.id)
    attrs = svc.attributes(88)
    assert attrs["display_name"] == "Попыч"  # override
    assert attrs["accent_color"] == 0x9B59B6  # дефолт остался


# --- P3: мягкая личность ---


async def test_set_identity_roundtrip(svc):
    persona = await svc.create_persona("Identity")
    await svc.assign(11, persona.id)
    await svc.set_identity(
        persona.id,
        {
            "display_name": "Попыч",
            "signature": "🔥",
            "accent_color": 0x112233,
            "presence": ["читает Мураками", " пьёт кофе "],
        },
    )
    attrs = svc.attributes(11)
    assert attrs["display_name"] == "Попыч"
    assert attrs["accent_color"] == 0x112233
    assert svc.accent_color(11) == 0x112233
    assert attrs["presence"] == ["читает Мураками", "пьёт кофе"]  # строки чистятся


async def test_set_identity_default_value_removes_override(svc):
    persona = await svc.create_persona("Identity")
    await svc.set_identity(persona.id, {"display_name": "Попыч"})
    # возврат к дефолтному значению снимает override, а не хранит копию дефолта
    await svc.set_identity(persona.id, {"display_name": "Попося"})
    assert svc.get(persona.id).attributes == {}


async def test_set_identity_validates(svc):
    persona = await svc.create_persona("Identity")
    with pytest.raises(ValueError):
        await svc.set_identity(persona.id, {"accent_color": -5})
    with pytest.raises(ValueError):
        await svc.set_identity(persona.id, {"accent_color": 0x1000000})
    with pytest.raises(ValueError):
        await svc.set_identity(persona.id, {"presence": "не список"})
    with pytest.raises(ValueError):
        await svc.set_identity(persona.id, {"nope": 1})


async def test_presence_lines_collects_default_and_assigned(svc):
    # дефолт без presence + назначенная персона со строками -> только её строки
    persona = await svc.create_persona("Ночная")
    await svc.set_identity(persona.id, {"presence": ["гуляет", "спит"]})
    await svc.assign(500, persona.id)
    assert svc.presence_lines() == ["гуляет", "спит"]
    # вторая назначенная персона добавляет свои строки без дублей
    other = await svc.create_persona("Дневная")
    await svc.set_identity(other.id, {"presence": ["спит", "работает"]})
    await svc.assign(501, other.id)
    assert svc.presence_lines() == ["гуляет", "спит", "работает"]


async def test_presence_lines_empty_without_overrides(svc):
    assert svc.presence_lines() == []


async def test_render_prompt_injects_identity_vars(svc):
    persona = await svc.create_persona("Шаблонная")
    await svc.update_persona(persona.id, prompt="Ты — {{display_name}} {{signature}}.")
    await svc.set_identity(persona.id, {"display_name": "Попыч", "signature": "🔥"})
    await svc.assign(66, persona.id)
    assert svc.render_prompt(66) == "Ты — Попыч 🔥."
    # явные переменные вызова важнее identity
    assert svc.render_prompt(66, {"display_name": "Кто-то"}) == "Ты — Кто-то 🔥."


async def test_reload_hook_fires(svc):
    calls = []

    async def hook():
        calls.append(1)

    svc.add_reload_hook(hook)
    await svc.reload()
    assert calls == [1]


# --- P4: render_block и find-replace ---


async def test_render_block_static_by_default(svc):
    # без AI-функции блок отдаёт статику с подстановкой
    text = await svc.render_block(1, "activity.welcome", None, name="Кот")
    assert text == "Добро пожаловать, Кот. Осмотрись, правила почитай. ✂️👁🖤"


async def test_render_block_uses_ai_instruction(svc):
    seen: list[str] = []

    async def ai(instruction: str) -> str:
        seen.append(instruction)
        return "AI-ответ"

    text = await svc.render_block(1, "activity.welcome", ai, name="Кот")
    assert text == "AI-ответ"
    assert seen and "Кот" in seen[0]  # инструкция отрендерена с переменными


async def test_render_block_falls_back_when_ai_fails(svc):
    async def ai(_instruction: str) -> str:
        raise RuntimeError("провайдер лёг")

    text = await svc.render_block(1, "activity.farewell", ai, name="Игорь")
    assert text == "Игорь ушёл. Бывает."


async def test_render_block_static_mode_skips_ai(svc):
    persona = await svc.create_persona("Статичная")
    await svc.assign(700, persona.id)
    await svc.set_phrase(persona.id, "activity.welcome", "Привет, {name}.", mode="static")

    async def ai(_instruction: str) -> str:
        raise AssertionError("AI не должен вызываться в режиме static")

    assert await svc.render_block(700, "activity.welcome", ai, name="Ия") == "Привет, Ия."


async def test_render_block_silent_returns_none(svc):
    persona = await svc.create_persona("Молчунья")
    await svc.assign(701, persona.id)
    await svc.set_phrase(persona.id, "activity.welcome", "не важно {name}", mode="silent")
    assert await svc.render_block(701, "activity.welcome", None, name="X") is None


async def test_render_block_empty_static_is_silent(svc):
    # AI-only блок (пустая статика): без AI — молчим
    assert await svc.render_block(1, "activity.return", None, name="X", days=9) is None


async def test_render_block_list_picks_random_item(svc):
    text = await svc.render_block(1, "activity.album", None)
    from src.application.persona.registry import PHRASE_SPECS

    assert text in PHRASE_SPECS["activity.album"].default


async def test_set_phrase_validates_placeholders(svc):
    persona = await svc.create_persona("X")
    with pytest.raises(ValueError):
        await svc.set_phrase(persona.id, "activity.welcome", "Привет, {nmae}!")  # опечатка
    with pytest.raises(ValueError):
        await svc.set_phrase(persona.id, "activity.welcome", 42)  # не строка


async def test_replace_phrases_dry_run_and_apply(svc):
    persona = await svc.create_persona("Замены")
    await svc.assign(702, persona.id)
    # dry_run: изменения видны, но ничего не записано
    preview = await svc.replace_phrases(persona.id, "ушёл", "свалил", dry_run=True)
    assert any(c["key"] == "activity.farewell" for c in preview)
    assert svc.phrase(702, "activity.farewell", name="X") == "X ушёл. Бывает."
    # apply: совпадение в дефолте становится override
    await svc.replace_phrases(persona.id, "ушёл", "свалил")
    assert svc.phrase(702, "activity.farewell", name="X") == "X свалил. Бывает."


async def test_replace_phrases_rejects_empty_find(svc):
    with pytest.raises(ValueError):
        await svc.replace_phrases(1, "", "x")
