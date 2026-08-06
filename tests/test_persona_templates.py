"""Готовые шаблоны персон из папки persona-templates/ обязаны импортироваться
БЕЗ единой отброшенной строки. Это и проверка авторской вёрстки примеров, и
страховка от дрейфа: если ключ фразы уедет из реестра, пример покраснеет тут,
а не тихо потеряет строку у пользователя."""

import json
from pathlib import Path

import pytest

from src.config import Settings
from src.infrastructure.persona_service import PersonaService

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "persona-templates"


def _template_files() -> list[Path]:
    return sorted(TEMPLATES_DIR.glob("*.json"))


def test_templates_folder_has_examples():
    assert _template_files(), f"нет ни одного шаблона персоны в {TEMPLATES_DIR}"


@pytest.mark.parametrize("path", _template_files(), ids=lambda p: p.name)
async def test_example_template_imports_clean(path: Path, session_factory):
    service = PersonaService(Settings(_env_file=None, discord_token="t"), session_factory)
    await service.load_all()  # как прод-старт: поднимает дефолтную персону
    data = json.loads(path.read_text(encoding="utf-8"))

    persona, report = await service.import_persona(data)

    assert persona.id > 0
    assert not persona.is_default
    assert report["attributes_ignored"] == [], (
        f"{path.name}: атрибуты {report['attributes_ignored']}"
    )
    assert report["phrases_ignored"] == [], f"{path.name}: фразы {report['phrases_ignored']}"
    # у примеров с переопределениями хотя бы что-то должно приняться
    assert report["phrases_accepted"] == len(data.get("phrases", []))
