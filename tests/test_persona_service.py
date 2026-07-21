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
