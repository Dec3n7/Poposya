"""Accent-цвет эмбедов из персоны сервера (мягкая личность, P3).

Модульный синглтон вместо DI: цвет нужен в каждом эмбеде десятка когов, и
протаскивать PersonaService через все контейнеры ради одного int дороже, чем
одна точка инициализации. client.setup_hook вызывает set_persona_service()
один раз на старте; без инициализации (юнит-тесты когов, ранний старт) —
дефолтный фиолетовый Попоси.
"""

from src.application.persona.registry import DEFAULT_ATTRIBUTES

_default = DEFAULT_ATTRIBUTES["accent_color"]
assert isinstance(_default, int)
DEFAULT_ACCENT: int = _default

_service = None  # PersonaService | None (без импорта — избегаем цикла)


def set_persona_service(service) -> None:
    global _service
    _service = service


def accent(guild_id: int | None) -> int:
    """Цвет эмбеда для сервера; None (DM/нет контекста) или сбой -> дефолт."""
    if _service is None or guild_id is None:
        return DEFAULT_ACCENT
    try:
        return _service.accent_color(guild_id)
    except Exception:
        return DEFAULT_ACCENT
