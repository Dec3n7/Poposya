"""Форматирование дайджеста: тон от статистики + факты/шаблон. Чистые функции."""

from datetime import date

from src.application.digest.format import (
    TONE_HINTS,
    DigestLine,
    DigestView,
    digest_tone,
    facts_block,
    render_plain,
    weekday_name,
)


def _view(**over) -> DigestView:
    base = dict(
        week_start=date(2026, 7, 27),
        week_end=date(2026, 8, 2),
        messages=100,
        messages_delta=0,
        voice_hours=10,
        voice_delta=0,
        members_delta=0,
        peak_day_name="среда",
        peak_day_messages=40,
        stars=(DigestLine("Аня", "1200 очк."),),
        birthdays=(),
        top_collector=None,
        watched_titles=(),
    )
    base.update(over)
    return DigestView(**base)


def test_weekday_name_ru():
    assert weekday_name(date(2026, 8, 2)) == "воскресенье"  # 2 авг 2026 — вс


def test_tone_welcoming_on_growth():
    assert digest_tone(_view(members_delta=5)) == "welcoming"


def test_tone_festive_on_birthdays():
    assert digest_tone(_view(birthdays=(("A", 1), ("B", 3)))) == "festive"


def test_tone_lively_on_message_surge():
    assert digest_tone(_view(messages=100, messages_delta=40)) == "lively"


def test_tone_quiet_when_sparse():
    assert digest_tone(_view(messages=8)) == "quiet"


def test_tone_quiet_on_steep_drop():
    assert digest_tone(_view(messages=100, messages_delta=-40)) == "quiet"


def test_tone_cozy_default():
    assert digest_tone(_view(messages=100, messages_delta=5)) == "cozy"


def test_every_tone_has_hint():
    for view in (
        _view(members_delta=5),
        _view(birthdays=(("A", 1), ("B", 2))),
        _view(messages=100, messages_delta=40),
        _view(messages=8),
        _view(messages=100, messages_delta=5),
    ):
        assert digest_tone(view) in TONE_HINTS


def test_facts_block_carries_numbers_and_delta():
    facts = facts_block(_view(messages=100, messages_delta=30))
    assert "100" in facts
    assert "+30" in facts  # дельта видна
    assert "Аня (1200 очк.)" in facts


def test_facts_block_omits_empty_sections():
    facts = facts_block(_view(members_delta=0, birthdays=(), watched_titles=(), top_collector=None))
    assert "Участников" not in facts
    assert "дни рождения" not in facts
    assert "киноклуб" not in facts
    assert "Коллекционер" not in facts


def test_render_plain_has_header_and_sections():
    view = _view(
        members_delta=3,
        birthdays=(("Боря", 0),),
        watched_titles=("Матрица",),
        top_collector=DigestLine("Гена", "42 наход."),
    )
    text = render_plain(view)
    assert text.startswith("🌙")
    assert "Итоги недели" in text
    assert "Боря (сегодня)" in text
    assert "Матрица" in text
    assert "Гена" in text


def test_render_plain_growth_and_decline_wording():
    assert "стало на 3 больше" in render_plain(_view(members_delta=3))
    assert "на 2 меньше" in render_plain(_view(members_delta=-2))
