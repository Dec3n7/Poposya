"""Сверка реестра PHRASE_SPECS — аналог test_guild_config_schema для настроек.

Ловит рассинхрон дефолтов/плейсхолдеров/режимов до того, как он утечёт в БД или
сломает .format при выносе строк в P4."""

import string

from src.application.persona.registry import (
    ALL_MODES,
    DEFAULT_ATTRIBUTES,
    PHRASE_SPECS,
)


def test_specs_well_formed():
    assert PHRASE_SPECS  # реестр непуст
    for key, spec in PHRASE_SPECS.items():
        assert spec.key == key
        assert spec.category
        assert spec.kind in {"str", "template", "list", "dict"}
        assert spec.allowed_modes, key
        assert set(spec.allowed_modes) <= set(ALL_MODES), key


def test_default_type_matches_kind():
    for spec in PHRASE_SPECS.values():
        if spec.kind in ("str", "template"):
            assert isinstance(spec.default, str), spec.key
        elif spec.kind == "list":
            assert isinstance(spec.default, list), spec.key
            assert all(isinstance(item, str) for item in spec.default), spec.key
        elif spec.kind == "dict":
            assert isinstance(spec.default, dict), spec.key


def test_template_placeholders_declared_and_renderable():
    """Плейсхолдеры в дефолте template-фразы ⊆ задекларированных (без опечаток
    и неизвестных {переменных}; дефолт может использовать не все — например,
    пустая статика «молчать»); дефолт форматируется без KeyError."""
    for spec in PHRASE_SPECS.values():
        if spec.kind != "template":
            continue
        used = {name for _, name, _, _ in string.Formatter().parse(spec.default) if name}
        assert used <= set(spec.placeholders), spec.key
        # не должно бросить KeyError — значит других плейсхолдеров в тексте нет
        spec.default.format(**{name: "x" for name in spec.placeholders})


def test_non_template_specs_have_no_placeholders():
    for spec in PHRASE_SPECS.values():
        if spec.kind != "template":
            assert not spec.placeholders, spec.key


def test_default_attributes_shape():
    assert {"display_name", "signature", "accent_color", "presence"} <= set(DEFAULT_ATTRIBUTES)
    assert isinstance(DEFAULT_ATTRIBUTES["accent_color"], int)
    assert isinstance(DEFAULT_ATTRIBUTES["presence"], list)
